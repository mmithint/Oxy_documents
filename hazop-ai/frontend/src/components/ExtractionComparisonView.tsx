import type { ExtractionCompareResponse } from "../types/hazop";

interface Props {
  result: ExtractionCompareResponse;
  onClose: () => void;
}

function ms(d: number): string {
  return d >= 1000 ? `${(d / 1000).toFixed(1)}s` : `${d}ms`;
}

function TagRow({
  item,
  highlight,
}: {
  item: Record<string, unknown>;
  highlight: "gpt" | "claude" | "both";
}) {
  const tag = String(item.tag ?? "");
  const type = String(item.equipment_type ?? item.instrument_type ?? "");
  const role = item.instrument_role ? String(item.instrument_role) : null;

  const rowClass =
    highlight === "gpt"
      ? "bg-yellow-50"
      : highlight === "claude"
      ? "bg-blue-50"
      : "";

  return (
    <tr className={rowClass}>
      <td className="px-3 py-1.5 font-mono text-xs text-gray-900 whitespace-nowrap">{tag}</td>
      <td className="px-3 py-1.5 text-xs text-gray-600">{type}</td>
      {role !== null && (
        <td className="px-3 py-1.5 text-xs text-gray-500">{role}</td>
      )}
    </tr>
  );
}

function SectionTable({
  title,
  items,
  highlight,
  showRole,
}: {
  title: string;
  items: Record<string, unknown>[];
  highlight: "gpt" | "claude" | "both";
  showRole?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mb-4">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">{title}</p>
      <table className="w-full border border-gray-200 rounded text-xs">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-3 py-1.5 text-left font-medium text-gray-600">Tag</th>
            <th className="px-3 py-1.5 text-left font-medium text-gray-600">Type</th>
            {showRole && (
              <th className="px-3 py-1.5 text-left font-medium text-gray-600">Role</th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item, i) => (
            <TagRow key={i} item={item} highlight={highlight} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ExtractionComparisonView({ result, onClose }: Props) {
  const {
    file_name,
    ocr_chunks_count,
    pages_count,
    gpt_equipment,
    gpt_instruments,
    gpt_duration_ms,
    gpt_error,
    claude_equipment,
    claude_instruments,
    claude_duration_ms,
    claude_error,
    tags_in_both,
    tags_only_in_gpt,
    tags_only_in_claude,
  } = result;

  // Split equipment and instruments into "both" vs "only" sets
  const gptOnlyEqTags = new Set(tags_only_in_gpt);
  const claudeOnlyEqTags = new Set(tags_only_in_claude);

  const gptEqBoth = gpt_equipment.filter((e) => !gptOnlyEqTags.has(String(e.tag ?? "").toUpperCase()));
  const gptEqOnly = gpt_equipment.filter((e) => gptOnlyEqTags.has(String(e.tag ?? "").toUpperCase()));
  const gptInstBoth = gpt_instruments.filter((i) => !gptOnlyEqTags.has(String(i.tag ?? "").toUpperCase()));
  const gptInstOnly = gpt_instruments.filter((i) => gptOnlyEqTags.has(String(i.tag ?? "").toUpperCase()));

  const claudeEqBoth = claude_equipment.filter((e) => !claudeOnlyEqTags.has(String(e.tag ?? "").toUpperCase()));
  const claudeEqOnly = claude_equipment.filter((e) => claudeOnlyEqTags.has(String(e.tag ?? "").toUpperCase()));
  const claudeInstBoth = claude_instruments.filter((i) => !claudeOnlyEqTags.has(String(i.tag ?? "").toUpperCase()));
  const claudeInstOnly = claude_instruments.filter((i) => claudeOnlyEqTags.has(String(i.tag ?? "").toUpperCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 overflow-y-auto p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-6xl my-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50 rounded-t-xl">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">GPT-4 vs Claude — P&ID Extraction Comparison</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {file_name} &nbsp;|&nbsp; {pages_count} page{pages_count !== 1 ? "s" : ""} &nbsp;|&nbsp; {ocr_chunks_count} OCR chunks
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-1"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="grid grid-cols-3 divide-x divide-gray-200">
          {/* Column 1: Diff Summary */}
          <div className="p-4">
            <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Diff Summary</h3>

            <div className="space-y-2 mb-4">
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-600">Found by both</span>
                <span className="font-semibold text-gray-900">{tags_in_both.length}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-yellow-700">GPT only</span>
                <span className="font-semibold text-yellow-800">{tags_only_in_gpt.length}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-blue-700">Claude only</span>
                <span className="font-semibold text-blue-800">{tags_only_in_claude.length}</span>
              </div>
            </div>

            {tags_only_in_gpt.length > 0 && (
              <div className="mb-3">
                <p className="text-xs font-medium text-yellow-700 mb-1">GPT-only tags</p>
                <div className="flex flex-wrap gap-1">
                  {tags_only_in_gpt.map((t) => (
                    <span key={t} className="bg-yellow-100 text-yellow-800 text-xs px-1.5 py-0.5 rounded font-mono">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {tags_only_in_claude.length > 0 && (
              <div>
                <p className="text-xs font-medium text-blue-700 mb-1">Claude-only tags</p>
                <div className="flex flex-wrap gap-1">
                  {tags_only_in_claude.map((t) => (
                    <span key={t} className="bg-blue-100 text-blue-800 text-xs px-1.5 py-0.5 rounded font-mono">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Column 2: GPT-4 */}
          <div className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">GPT-4 (Azure OpenAI)</h3>
              <span className="text-xs text-gray-500">⏱ {ms(gpt_duration_ms)}</span>
            </div>

            {gpt_error && (
              <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700">
                {gpt_error}
              </div>
            )}

            <div className="text-xs text-gray-500 mb-3">
              Equipment: <strong>{gpt_equipment.length}</strong> &nbsp;|&nbsp; Instruments: <strong>{gpt_instruments.length}</strong>
            </div>

            <SectionTable title="Equipment (both)" items={gptEqBoth} highlight="both" />
            <SectionTable title="Equipment (GPT only)" items={gptEqOnly} highlight="gpt" />
            <SectionTable title="Instruments (both)" items={gptInstBoth} highlight="both" showRole />
            <SectionTable title="Instruments (GPT only)" items={gptInstOnly} highlight="gpt" showRole />
          </div>

          {/* Column 3: Claude */}
          <div className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Claude Sonnet</h3>
              <span className="text-xs text-gray-500">⏱ {ms(claude_duration_ms)}</span>
            </div>

            {claude_error && (
              <div className="mb-3 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700">
                {claude_error}
              </div>
            )}

            <div className="text-xs text-gray-500 mb-3">
              Equipment: <strong>{claude_equipment.length}</strong> &nbsp;|&nbsp; Instruments: <strong>{claude_instruments.length}</strong>
            </div>

            <SectionTable title="Equipment (both)" items={claudeEqBoth} highlight="both" />
            <SectionTable title="Equipment (Claude only)" items={claudeEqOnly} highlight="claude" />
            <SectionTable title="Instruments (both)" items={claudeInstBoth} highlight="both" showRole />
            <SectionTable title="Instruments (Claude only)" items={claudeInstOnly} highlight="claude" showRole />
          </div>
        </div>

        {/* Legend */}
        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 rounded-b-xl flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded bg-yellow-100 border border-yellow-300" />
            Yellow = GPT found, Claude missed
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded bg-blue-100 border border-blue-300" />
            Blue = Claude found, GPT missed
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded bg-white border border-gray-300" />
            White = both found
          </span>
        </div>
      </div>
    </div>
  );
}
