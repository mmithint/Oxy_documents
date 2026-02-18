import { useState } from "react";

interface MergeSummary {
  path1_text: {
    equipment_count: number;
    instrument_count: number;
    equipment_tags: string[];
    instrument_tags: string[];
  };
  path2_vision: {
    equipment_count: number;
    instrument_count: number;
    equipment_tags: string[];
    instrument_tags: string[];
  };
  merged_final: {
    equipment_count: number;
    instrument_count: number;
    equipment_tags: string[];
    instrument_tags: string[];
  };
  vision_added: {
    equipment_tags: string[];
    instrument_tags: string[];
    equipment_count: number;
    instrument_count: number;
  };
}

interface ExtractionDetailsProps {
  ocrChunks: string[];
  llmRawOutput: Record<string, unknown> | null;
  visionRawOutput: Record<string, unknown> | null;
  mergeSummary: Record<string, unknown> | null;
}

export default function ExtractionDetails({
  ocrChunks,
  llmRawOutput,
  visionRawOutput,
  mergeSummary,
}: ExtractionDetailsProps) {
  const [showChunks, setShowChunks] = useState(false);
  const [showLlm, setShowLlm] = useState(false);
  const [showVision, setShowVision] = useState(false);
  const [showMerge, setShowMerge] = useState(false);

  const hasContent = ocrChunks.length > 0 || llmRawOutput || visionRawOutput || mergeSummary;
  if (!hasContent) return null;

  const merge = mergeSummary as MergeSummary | null;

  return (
    <div className="space-y-3 mb-4">
      {/* Merge Summary Section (shown first — most useful) */}
      {merge && (
        <div className="bg-white rounded-lg border border-gray-200">
          <button
            onClick={() => setShowMerge(!showMerge)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-50"
          >
            <span className="flex items-center gap-2">
              Extraction Summary (Text + Vision Merge)
              {merge.vision_added && (merge.vision_added.equipment_count > 0 || merge.vision_added.instrument_count > 0) && (
                <span className="px-2 py-0.5 text-[10px] font-medium bg-purple-100 text-purple-700 rounded-full">
                  +{merge.vision_added.equipment_count + merge.vision_added.instrument_count} from Vision
                </span>
              )}
            </span>
            <span className="text-gray-400 text-xs">
              {showMerge ? "Collapse" : "Expand"}
            </span>
          </button>
          {showMerge && (
            <div className="px-4 pb-4 space-y-4">
              {/* Summary Cards */}
              <div className="grid grid-cols-3 gap-3">
                {/* Path 1: Text */}
                <div className="bg-blue-50 rounded-lg border border-blue-200 p-3">
                  <div className="text-[10px] font-semibold text-blue-600 uppercase tracking-wide mb-2">
                    Path 1: OCR + Text LLM
                  </div>
                  <div className="text-lg font-bold text-blue-800">
                    {merge.path1_text.equipment_count} eq / {merge.path1_text.instrument_count} inst
                  </div>
                  <div className="mt-2 space-y-1">
                    {merge.path1_text.equipment_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {merge.path1_text.equipment_tags.map((tag) => (
                          <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-blue-100 text-blue-700 rounded font-mono">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {merge.path1_text.instrument_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {merge.path1_text.instrument_tags.map((tag) => (
                          <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-blue-50 text-blue-600 rounded font-mono border border-blue-200">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Path 2: Vision */}
                <div className="bg-purple-50 rounded-lg border border-purple-200 p-3">
                  <div className="text-[10px] font-semibold text-purple-600 uppercase tracking-wide mb-2">
                    Path 2: GPT-4 Vision
                  </div>
                  <div className="text-lg font-bold text-purple-800">
                    {merge.path2_vision.equipment_count} eq / {merge.path2_vision.instrument_count} inst
                  </div>
                  <div className="mt-2 space-y-1">
                    {merge.path2_vision.equipment_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {merge.path2_vision.equipment_tags.map((tag) => (
                          <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-purple-100 text-purple-700 rounded font-mono">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {merge.path2_vision.instrument_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {merge.path2_vision.instrument_tags.map((tag) => (
                          <span key={tag} className="px-1.5 py-0.5 text-[10px] bg-purple-50 text-purple-600 rounded font-mono border border-purple-200">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Merged Final */}
                <div className="bg-green-50 rounded-lg border border-green-200 p-3">
                  <div className="text-[10px] font-semibold text-green-600 uppercase tracking-wide mb-2">
                    Merged Final Result
                  </div>
                  <div className="text-lg font-bold text-green-800">
                    {merge.merged_final.equipment_count} eq / {merge.merged_final.instrument_count} inst
                  </div>
                  <div className="mt-2 space-y-1">
                    {merge.merged_final.equipment_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {merge.merged_final.equipment_tags.map((tag) => {
                          const isVisionOnly = merge.vision_added.equipment_tags.includes(tag);
                          return (
                            <span
                              key={tag}
                              className={`px-1.5 py-0.5 text-[10px] rounded font-mono ${
                                isVisionOnly
                                  ? "bg-purple-200 text-purple-800 border border-purple-300 font-bold"
                                  : "bg-green-100 text-green-700"
                              }`}
                              title={isVisionOnly ? "Added by Vision (not found by OCR)" : "Found by OCR text"}
                            >
                              {tag}{isVisionOnly ? " *" : ""}
                            </span>
                          );
                        })}
                      </div>
                    )}
                    {merge.merged_final.instrument_tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {merge.merged_final.instrument_tags.map((tag) => {
                          const isVisionOnly = merge.vision_added.instrument_tags.includes(tag);
                          return (
                            <span
                              key={tag}
                              className={`px-1.5 py-0.5 text-[10px] rounded font-mono border ${
                                isVisionOnly
                                  ? "bg-purple-100 text-purple-800 border-purple-300 font-bold"
                                  : "bg-green-50 text-green-600 border-green-200"
                              }`}
                              title={isVisionOnly ? "Added by Vision (not found by OCR)" : "Found by OCR text"}
                            >
                              {tag}{isVisionOnly ? " *" : ""}
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Vision-only additions highlight */}
              {merge.vision_added && (merge.vision_added.equipment_count > 0 || merge.vision_added.instrument_count > 0) && (
                <div className="bg-purple-50 rounded-lg border border-purple-200 p-3">
                  <div className="text-xs font-semibold text-purple-700 mb-1">
                    Tags found ONLY by Vision (OCR missed these):
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {merge.vision_added.equipment_tags.map((tag) => (
                      <span key={tag} className="px-2 py-1 text-xs bg-purple-200 text-purple-800 rounded font-mono font-bold">
                        {tag} (equipment)
                      </span>
                    ))}
                    {merge.vision_added.instrument_tags.map((tag) => (
                      <span key={tag} className="px-2 py-1 text-xs bg-purple-200 text-purple-800 rounded font-mono font-bold">
                        {tag} (instrument)
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {merge.vision_added && merge.vision_added.equipment_count === 0 && merge.vision_added.instrument_count === 0 && (
                <div className="text-xs text-gray-500 italic px-1">
                  Both paths found the same tags — no additional items from Vision.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* OCR Chunks Section */}
      {ocrChunks.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200">
          <button
            onClick={() => setShowChunks(!showChunks)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-50"
          >
            <span>Path 1: Raw OCR Text Chunks ({ocrChunks.length})</span>
            <span className="text-gray-400 text-xs">
              {showChunks ? "Collapse" : "Expand"}
            </span>
          </button>
          {showChunks && (
            <div className="px-4 pb-4 max-h-96 overflow-y-auto space-y-3">
              {ocrChunks.map((chunk, i) => (
                <div key={i} className="bg-gray-50 rounded border border-gray-100 p-3">
                  <div className="text-[10px] text-gray-400 mb-1 font-medium">
                    Chunk {i + 1} / {ocrChunks.length} — {chunk.length} chars
                  </div>
                  <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono leading-relaxed">
                    {chunk}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* LLM Text Extraction Output Section */}
      {llmRawOutput && (
        <div className="bg-white rounded-lg border border-gray-200">
          <button
            onClick={() => setShowLlm(!showLlm)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-50"
          >
            <span className="flex items-center gap-2">
              Path 1: LLM Text Extraction Output
              <span className="px-2 py-0.5 text-[10px] font-medium bg-blue-100 text-blue-600 rounded-full">
                OCR → Text
              </span>
            </span>
            <span className="text-gray-400 text-xs">
              {showLlm ? "Collapse" : "Expand"}
            </span>
          </button>
          {showLlm && (
            <div className="px-4 pb-4 max-h-96 overflow-y-auto">
              <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono bg-gray-50 rounded border border-gray-100 p-3 leading-relaxed">
                {JSON.stringify(llmRawOutput, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Vision Extraction Output Section */}
      {visionRawOutput && (
        <div className="bg-white rounded-lg border border-gray-200">
          <button
            onClick={() => setShowVision(!showVision)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-50"
          >
            <span className="flex items-center gap-2">
              Path 2: GPT-4 Vision Extraction Output
              <span className="px-2 py-0.5 text-[10px] font-medium bg-purple-100 text-purple-600 rounded-full">
                Image → Vision
              </span>
            </span>
            <span className="text-gray-400 text-xs">
              {showVision ? "Collapse" : "Expand"}
            </span>
          </button>
          {showVision && (
            <div className="px-4 pb-4 max-h-96 overflow-y-auto">
              <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono bg-purple-50 rounded border border-purple-100 p-3 leading-relaxed">
                {JSON.stringify(visionRawOutput, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
