import { useState, useRef } from "react";
import { uploadPID, uploadKnowledgeDocument } from "../services/api";
import type { PIDNode } from "../types/hazop";

interface UploadPanelProps {
  onNodesExtracted: (nodes: PIDNode[]) => void;
  onExtractionDetails?: (
    chunks: string[],
    llmOutput: Record<string, unknown> | null,
    visionOutput: Record<string, unknown> | null,
    mergeSummary: Record<string, unknown> | null,
  ) => void;
}

interface FileProgress {
  name: string;
  status: "pending" | "processing" | "done" | "error";
  message?: string;
}

export default function UploadPanel({ onNodesExtracted, onExtractionDetails }: UploadPanelProps) {
  const [pidFiles, setPidFiles] = useState<File[]>([]);
  const [knowledgeFiles, setKnowledgeFiles] = useState<File[]>([]);
  const [docType, setDocType] = useState("consequence_guidance");
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [confidence, setConfidence] = useState<number | null>(null);

  // Progress modal state
  const [showProgress, setShowProgress] = useState(false);
  const [progressTitle, setProgressTitle] = useState("");
  const [progressFiles, setProgressFiles] = useState<FileProgress[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [totalFiles, setTotalFiles] = useState(0);

  const pidInputRef = useRef<HTMLInputElement>(null);
  const knowledgeInputRef = useRef<HTMLInputElement>(null);

  // ---- P&ID Upload (multi-file) ----
  const handlePIDUpload = async () => {
    if (pidFiles.length === 0) return;
    setUploading(true);
    setUploadMessage("");
    setConfidence(null);

    const fileList: FileProgress[] = pidFiles.map((f) => ({
      name: f.name,
      status: "pending" as const,
    }));
    setProgressFiles(fileList);
    setTotalFiles(pidFiles.length);
    setCurrentIndex(0);
    setProgressTitle("Processing P&ID Files");
    setShowProgress(true);

    const allNodes: PIDNode[] = [];
    let lastChunks: string[] = [];
    let lastLlmOutput: Record<string, unknown> | null = null;
    let lastVisionOutput: Record<string, unknown> | null = null;
    let lastMergeSummary: Record<string, unknown> | null = null;
    let lastConfidence: number | null = null;
    let errorCount = 0;

    for (let i = 0; i < pidFiles.length; i++) {
      setCurrentIndex(i);
      setProgressFiles((prev) =>
        prev.map((f, idx) =>
          idx === i ? { ...f, status: "processing" } : f
        )
      );

      try {
        const result = await uploadPID(pidFiles[i]);
        allNodes.push(...result.nodes);
        lastChunks = result.ocr_chunks ?? [];
        lastLlmOutput = result.llm_raw_output ?? null;
        lastVisionOutput = result.vision_raw_output ?? null;
        lastMergeSummary = result.merge_summary ?? null;
        lastConfidence = result.confidence_score;

        setProgressFiles((prev) =>
          prev.map((f, idx) =>
            idx === i
              ? { ...f, status: "done", message: result.message }
              : f
          )
        );
      } catch (err: unknown) {
        errorCount++;
        const msg = err instanceof Error ? err.message : "Upload failed";
        setProgressFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: "error", message: msg } : f
          )
        );
      }
    }

    // Finished all files
    setCurrentIndex(pidFiles.length);
    if (allNodes.length > 0) {
      onNodesExtracted(allNodes);
      onExtractionDetails?.(lastChunks, lastLlmOutput, lastVisionOutput, lastMergeSummary);
      setConfidence(lastConfidence);
    }
    const successCount = pidFiles.length - errorCount;
    setUploadMessage(
      `P&ID upload complete: ${successCount} succeeded, ${errorCount} failed`
    );
    setUploading(false);
    setPidFiles([]);
    if (pidInputRef.current) pidInputRef.current.value = "";
  };

  // ---- Knowledge Upload (multi-file) ----
  const handleKnowledgeUpload = async () => {
    if (knowledgeFiles.length === 0) return;
    setUploading(true);
    setUploadMessage("");

    const fileList: FileProgress[] = knowledgeFiles.map((f) => ({
      name: f.name,
      status: "pending" as const,
    }));
    setProgressFiles(fileList);
    setTotalFiles(knowledgeFiles.length);
    setCurrentIndex(0);
    setProgressTitle("Ingesting Knowledge Documents");
    setShowProgress(true);

    let totalChunks = 0;
    let errorCount = 0;

    for (let i = 0; i < knowledgeFiles.length; i++) {
      setCurrentIndex(i);
      setProgressFiles((prev) =>
        prev.map((f, idx) =>
          idx === i ? { ...f, status: "processing" } : f
        )
      );

      try {
        const result = await uploadKnowledgeDocument(knowledgeFiles[i], docType);
        const chunks = (result as Record<string, unknown>).total_chunks as number;
        totalChunks += chunks || 0;

        setProgressFiles((prev) =>
          prev.map((f, idx) =>
            idx === i
              ? { ...f, status: "done", message: `${chunks} chunks stored` }
              : f
          )
        );
      } catch (err: unknown) {
        errorCount++;
        const msg = err instanceof Error ? err.message : "Upload failed";
        setProgressFiles((prev) =>
          prev.map((f, idx) =>
            idx === i ? { ...f, status: "error", message: msg } : f
          )
        );
      }
    }

    setCurrentIndex(knowledgeFiles.length);
    const successCount = knowledgeFiles.length - errorCount;
    setUploadMessage(
      `Knowledge ingestion complete: ${successCount} docs, ${totalChunks} total chunks${errorCount > 0 ? `, ${errorCount} failed` : ""}`
    );
    setUploading(false);
    setKnowledgeFiles([]);
    if (knowledgeInputRef.current) knowledgeInputRef.current.value = "";
  };

  const closeProgress = () => {
    setShowProgress(false);
    setProgressFiles([]);
  };

  const isAllDone = currentIndex >= totalFiles;

  return (
    <div className="space-y-6">
      {/* P&ID Upload */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">Upload P&ID</h3>
        <div className="space-y-3">
          <input
            ref={pidInputRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp"
            multiple
            onChange={(e) => setPidFiles(Array.from(e.target.files || []))}
            className="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
          />
          {pidFiles.length > 1 && (
            <p className="text-xs text-blue-600">{pidFiles.length} files selected</p>
          )}
          <button
            onClick={handlePIDUpload}
            disabled={pidFiles.length === 0 || uploading}
            className="w-full px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {uploading ? "Processing..." : `Upload & Parse P&ID${pidFiles.length > 1 ? ` (${pidFiles.length})` : ""}`}
          </button>
        </div>
      </div>

      {/* Knowledge Document Upload */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-3">Upload Knowledge Document</h3>
        <div className="space-y-3">
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            className="block w-full text-sm border border-gray-300 rounded px-3 py-1.5"
          >
            <option value="consequence_guidance">Consequence Guidance</option>
            <option value="risk_matrix">Risk Matrix</option>
            <option value="barrier_philosophy">Barrier Philosophy</option>
            <option value="sop">Standard Operating Procedure</option>
            <option value="hazop_reference">HAZOP Reference</option>
          </select>
          <input
            ref={knowledgeInputRef}
            type="file"
            accept=".pdf"
            multiple
            onChange={(e) => setKnowledgeFiles(Array.from(e.target.files || []))}
            className="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100"
          />
          {knowledgeFiles.length > 1 && (
            <p className="text-xs text-emerald-600">{knowledgeFiles.length} files selected</p>
          )}
          <button
            onClick={handleKnowledgeUpload}
            disabled={knowledgeFiles.length === 0 || uploading}
            className="w-full px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {uploading ? "Ingesting..." : `Upload & Ingest${knowledgeFiles.length > 1 ? ` (${knowledgeFiles.length})` : ""}`}
          </button>
        </div>
      </div>

      {/* Status Messages */}
      {uploadMessage && !showProgress && (
        <div className={`p-3 rounded text-sm ${uploadMessage.includes("failed") || uploadMessage.startsWith("Error") ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"}`}>
          {uploadMessage}
          {confidence !== null && (
            <div className="mt-1 text-xs opacity-75">
              Extraction confidence: {(confidence * 100).toFixed(0)}%
            </div>
          )}
        </div>
      )}

      {/* Processing Progress Modal */}
      {showProgress && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
            {/* Header */}
            <div className="px-5 py-4 border-b border-gray-200 bg-gray-50">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-900">{progressTitle}</h3>
                <span className="text-xs text-gray-500">
                  {Math.min(currentIndex + 1, totalFiles)} / {totalFiles}
                </span>
              </div>
              {/* Overall progress bar */}
              <div className="mt-2 w-full bg-gray-200 rounded-full h-2">
                <div
                  className="h-2 rounded-full transition-all duration-500 bg-blue-500"
                  style={{ width: `${(Math.min(currentIndex + (isAllDone ? 0 : 1), totalFiles) / totalFiles) * 100}%` }}
                />
              </div>
            </div>

            {/* File list */}
            <div className="px-5 py-3 max-h-64 overflow-y-auto">
              <div className="space-y-2">
                {progressFiles.map((file, idx) => (
                  <div
                    key={idx}
                    className={`flex items-start gap-3 px-3 py-2 rounded-lg text-xs ${
                      file.status === "processing"
                        ? "bg-blue-50 border border-blue-200"
                        : file.status === "done"
                        ? "bg-green-50 border border-green-100"
                        : file.status === "error"
                        ? "bg-red-50 border border-red-100"
                        : "bg-gray-50 border border-gray-100"
                    }`}
                  >
                    {/* Status icon */}
                    <div className="mt-0.5 flex-shrink-0">
                      {file.status === "pending" && (
                        <span className="inline-block w-4 h-4 rounded-full border-2 border-gray-300" />
                      )}
                      {file.status === "processing" && (
                        <span className="inline-block w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
                      )}
                      {file.status === "done" && (
                        <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {file.status === "error" && (
                        <svg className="w-4 h-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      )}
                    </div>

                    {/* File info */}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-gray-800 truncate">{file.name}</p>
                      {file.status === "processing" && (
                        <p className="text-blue-600 mt-0.5">Processing... please wait</p>
                      )}
                      {file.status === "done" && file.message && (
                        <p className="text-green-600 mt-0.5">{file.message}</p>
                      )}
                      {file.status === "error" && file.message && (
                        <p className="text-red-600 mt-0.5">{file.message}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Footer */}
            <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-between">
              {!isAllDone ? (
                <p className="text-xs text-gray-500 animate-pulse">
                  Do not close this window while processing...
                </p>
              ) : (
                <p className="text-xs text-green-600 font-medium">
                  All files processed
                </p>
              )}
              <button
                onClick={closeProgress}
                disabled={!isAllDone}
                className="px-4 py-1.5 text-xs font-medium rounded bg-gray-200 text-gray-700 hover:bg-gray-300 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isAllDone ? "Close" : "Processing..."}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
