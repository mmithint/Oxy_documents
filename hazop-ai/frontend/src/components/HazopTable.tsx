import { useState } from "react";
import type { HAZOPReport, Deviation } from "../types/hazop";
import RiskBadge from "./RiskBadge";
import StatusBadge from "./StatusBadge";
import ApprovalControls from "./ApprovalControls";
import { exportReport } from "../services/api";

interface HazopTableProps {
  report: HAZOPReport;
  onRefresh: () => void;
}

export default function HazopTable({ report, onRefresh }: HazopTableProps) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [filterEquipment, setFilterEquipment] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);
    try {
      const blob = await exportReport(report.report_id!);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `HAZOP_${report.node_name.replace(/\s+/g, "_")}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // silently ignore; could add toast here
    } finally {
      setExporting(false);
    }
  };

  // Get unique equipment tags for filter
  const equipmentTags = [...new Set(report.deviations.map((d) => d.equipment_tag))];

  // Apply filters
  const filteredDeviations = report.deviations.filter((d) => {
    if (filterEquipment !== "all" && d.equipment_tag !== filterEquipment) return false;
    if (filterStatus !== "all" && d.status !== filterStatus) return false;
    return true;
  });

  const toggleRow = (id: string) => {
    setExpandedRow(expandedRow === id ? null : id);
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">
              HAZOP Report — {report.node_name}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {report.deviations.length} deviations | Version {report.version} | Status: {report.status}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">
              {report.deviations.filter((d) => d.status === "approved").length}/{report.deviations.length} approved
            </span>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="px-3 py-1.5 text-xs font-medium text-white bg-green-600 hover:bg-green-700 rounded disabled:opacity-50 flex items-center gap-1.5"
            >
              {exporting ? "Exporting..." : "⬇ Export to Excel"}
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex gap-3 mt-3">
          <select
            value={filterEquipment}
            onChange={(e) => setFilterEquipment(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1"
          >
            <option value="all">All Equipment</option>
            {equipmentTags.map((tag) => (
              <option key={tag} value={tag}>{tag}</option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1"
          >
            <option value="all">All Status</option>
            <option value="draft">Draft</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="revision_requested">Revision Requested</option>
          </select>
          <span className="text-xs text-gray-400 self-center">
            Showing {filteredDeviations.length} of {report.deviations.length}
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Equipment</th>
              <th className="text-left px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Deviation</th>
              <th className="text-left px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Causes</th>
              <th className="text-left px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Consequences</th>
              <th className="text-left px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Safeguards</th>
              <th className="text-center px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Risk</th>
              <th className="text-center px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Status</th>
              <th className="text-center px-4 py-2 text-xs font-semibold text-gray-600 uppercase tracking-wide">Review</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filteredDeviations.map((dev) => (
              <DeviationRow
                key={dev.deviation_id}
                deviation={dev}
                isExpanded={expandedRow === dev.deviation_id}
                onToggle={() => toggleRow(dev.deviation_id)}
                onStatusChanged={onRefresh}
              />
            ))}
          </tbody>
        </table>
      </div>

      {filteredDeviations.length === 0 && (
        <div className="px-4 py-8 text-center text-gray-400 text-sm">
          No deviations match the current filters
        </div>
      )}
    </div>
  );
}

// --- Individual Deviation Row ---

interface DeviationRowProps {
  deviation: Deviation;
  isExpanded: boolean;
  onToggle: () => void;
  onStatusChanged: () => void;
}

function DeviationRow({ deviation, isExpanded, onToggle, onStatusChanged }: DeviationRowProps) {
  const dev = deviation;

  return (
    <>
      <tr
        className="hover:bg-gray-50 cursor-pointer"
        onClick={onToggle}
      >
        {/* Equipment */}
        <td className="px-4 py-3">
          <span className="font-mono text-xs font-medium">{dev.equipment_tag}</span>
        </td>

        {/* Deviation */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5">
            <span className="font-medium text-gray-900 text-xs">{dev.deviation}</span>
            {dev.requires_mandatory_sme_review && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-amber-100 text-amber-700 rounded whitespace-nowrap">
                SME Review Required
              </span>
            )}
          </div>
          <div className="text-[10px] text-gray-400">{dev.guideword} + {dev.parameter}</div>
        </td>

        {/* Causes (truncated) */}
        <td className="px-4 py-3 max-w-[200px]">
          <div className="text-xs text-gray-700 truncate">
            {dev.causes[0] || "—"}
          </div>
          {dev.causes.length > 1 && (
            <span className="text-[10px] text-gray-400">+{dev.causes.length - 1} more</span>
          )}
        </td>

        {/* Consequences (truncated) */}
        <td className="px-4 py-3 max-w-[200px]">
          <div className="text-xs text-gray-700 truncate">
            {dev.consequences[0] || "—"}
          </div>
          {dev.consequences.length > 1 && (
            <span className="text-[10px] text-gray-400">+{dev.consequences.length - 1} more</span>
          )}
        </td>

        {/* Safeguards */}
        <td className="px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {dev.safeguards.slice(0, 3).map((sg) => (
              <span
                key={sg.instrument_tag}
                className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[10px] font-mono"
                title={`${sg.pr_classification} — ${sg.description}`}
              >
                {sg.instrument_tag}
              </span>
            ))}
            {dev.safeguards.length > 3 && (
              <span className="text-[10px] text-gray-400">+{dev.safeguards.length - 3}</span>
            )}
          </div>
        </td>

        {/* Risk */}
        <td className="px-4 py-3">
          <div className="flex flex-col gap-1 items-center">
            <RiskBadge score={dev.risk?.paf ?? null} label="PAF" />
            <RiskBadge score={dev.risk?.pd_lor ?? null} label="PD" />
          </div>
        </td>

        {/* Status */}
        <td className="px-4 py-3 text-center">
          <StatusBadge status={dev.status} />
        </td>

        {/* Review */}
        <td className="px-4 py-3 text-center" onClick={(e) => e.stopPropagation()}>
          <ApprovalControls deviation={dev} onStatusChanged={onStatusChanged} />
        </td>
      </tr>

      {/* Expanded Detail Row */}
      {isExpanded && (
        <tr className="bg-gray-50">
          <td colSpan={8} className="px-4 py-4">
            <ExpandedDeviationDetail deviation={dev} />
          </td>
        </tr>
      )}
    </>
  );
}

// --- Expanded Detail View ---

function ExpandedDeviationDetail({ deviation }: { deviation: Deviation }) {
  return (
    <div className="space-y-4 text-xs">
      <div className="grid grid-cols-4 gap-6">
        {/* Column 1: Causes + Drawing References */}
        <div className="space-y-4">
          <div>
            <h4 className="font-semibold text-gray-700 mb-2">Causes</h4>
            <ul className="space-y-1">
              {deviation.causes.map((cause, i) => (
                <li key={i} className="text-gray-600 flex items-start gap-1.5">
                  <span className="text-gray-400 mt-0.5">-</span>
                  {cause}
                </li>
              ))}
              {deviation.causes.length === 0 && (
                <li className="text-gray-400 italic">No causes listed</li>
              )}
            </ul>
          </div>

          {deviation.drawing_references.length > 0 && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-2">Drawing References</h4>
              <div className="flex flex-wrap gap-1">
                {deviation.drawing_references.map((ref, i) => (
                  <span
                    key={i}
                    className="px-1.5 py-0.5 bg-gray-100 text-gray-700 rounded text-[10px] font-mono border border-gray-200"
                  >
                    {ref}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Column 2: Intermediate Consequences + Final Impacts / Scenario */}
        <div className="space-y-4">
          {deviation.intermediate_consequences.length > 0 && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-2">Intermediate Consequences</h4>
              <ul className="space-y-1">
                {deviation.intermediate_consequences.map((ic, i) => (
                  <li key={i} className="text-gray-600 flex items-start gap-1.5">
                    <span className="text-gray-400 mt-0.5">-</span>
                    {ic}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h4 className="font-semibold text-gray-700 mb-2">Final Impacts (No Safeguards)</h4>
            <ul className="space-y-1">
              {deviation.consequences.map((cons, i) => (
                <li key={i} className="text-gray-600 flex items-start gap-1.5">
                  <span className="text-gray-400 mt-0.5">-</span>
                  {cons}
                </li>
              ))}
              {deviation.consequences.length === 0 && (
                <li className="text-gray-400 italic">No consequences listed</li>
              )}
            </ul>
          </div>

          {deviation.scenario_comments && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-1">Scenario Comments</h4>
              <p className="text-gray-600 leading-relaxed">{deviation.scenario_comments}</p>
            </div>
          )}

          {deviation.consequence_category && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500">Consequence Category:</span>
              <span className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-[10px] font-medium">
                {deviation.consequence_category}
              </span>
            </div>
          )}
        </div>

        {/* Column 3: Safeguards (with control_category, CME name) + PEC */}
        <div className="space-y-4">
          <div>
            <h4 className="font-semibold text-gray-700 mb-2">Safeguards</h4>
            <ul className="space-y-2">
              {deviation.safeguards.map((sg) => (
                <li key={sg.instrument_tag} className="text-gray-600">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-700 rounded text-[10px] font-mono">
                      {sg.pr_classification}
                    </span>
                    <span className="font-medium">{sg.instrument_tag}</span>
                    {sg.mitigation_type && (
                      <span className={`px-1 py-0.5 rounded text-[10px] ${
                        sg.mitigation_type === "CME"
                          ? "bg-red-50 text-red-700"
                          : "bg-yellow-50 text-yellow-700"
                      }`}>
                        {sg.mitigation_type}
                      </span>
                    )}
                    {sg.control_category && (
                      <span className="px-1 py-0.5 bg-purple-50 text-purple-700 rounded text-[10px]">
                        {sg.control_category}
                      </span>
                    )}
                  </div>
                  {sg.cme_name && (
                    <div className="text-[10px] text-gray-500 mt-0.5 ml-0.5">{sg.cme_name}</div>
                  )}
                  {sg.description && (
                    <div className="text-[10px] text-gray-400 mt-0.5 ml-0.5">{sg.description}</div>
                  )}
                </li>
              ))}
              {deviation.safeguards.length === 0 && (
                <li className="text-gray-400 italic">No safeguards identified</li>
              )}
            </ul>
          </div>

          {deviation.pec && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500">PEC (Personnel Exposure):</span>
              <span className="px-1.5 py-0.5 bg-orange-50 text-orange-700 rounded text-[10px] font-medium border border-orange-200">
                {deviation.pec}
              </span>
            </div>
          )}
        </div>

        {/* Column 4: Risk (current + planned residual) + Recommendations + Responsibility */}
        <div className="space-y-4">
          <div>
            <h4 className="font-semibold text-gray-700 mb-2">Current Risk</h4>
            <div className="flex flex-wrap gap-1.5">
              <RiskBadge score={deviation.risk?.paf ?? null} label="PAF" />
              <RiskBadge score={deviation.risk?.pd_lor ?? null} label="PD/LOR" />
              <RiskBadge score={deviation.risk?.ecr ?? null} label="ECR" />
            </div>
          </div>

          {deviation.planned_residual_risk && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-2">Planned Residual Risk</h4>
              <div className="flex flex-wrap gap-1.5">
                <RiskBadge score={deviation.planned_residual_risk.paf ?? null} label="PAF" />
                <RiskBadge score={deviation.planned_residual_risk.pd_lor ?? null} label="PD/LOR" />
                <RiskBadge score={deviation.planned_residual_risk.ecr ?? null} label="ECR" />
              </div>
            </div>
          )}

          {deviation.recommendations.length > 0 && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-2">Recommendations</h4>
              <ul className="space-y-1">
                {deviation.recommendations.map((rec, i) => (
                  <li key={i} className="text-gray-600 flex items-start gap-1.5">
                    <span className="text-amber-500 mt-0.5">!</span>
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {deviation.responsibility && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500">Responsibility:</span>
              <span className="font-medium text-gray-700">{deviation.responsibility}</span>
            </div>
          )}

          {deviation.review_comments && (
            <div>
              <h4 className="font-semibold text-gray-700 mb-1">Review Comments</h4>
              <p className="text-gray-500 italic">"{deviation.review_comments}"</p>
              <p className="text-[10px] text-gray-400 mt-1">— {deviation.reviewed_by}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
