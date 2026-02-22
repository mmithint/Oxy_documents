import { useState } from "react";
import ExcelJS from "exceljs";
import type { PIDNode, Equipment, Instrument } from "../types/hazop";
import { COMMON_EQUIPMENT_TYPES, COMMON_INSTRUMENT_TYPES } from "../types/hazop";
import { validateEquipment } from "../services/api";

// ---------------------------------------------------------------------------
// Helpers — role-based classification (reads instrument_role from data)
// ---------------------------------------------------------------------------

function isCauseInstrument(inst: Instrument): boolean {
  return inst.instrument_role === "cause";
}

function isSafeguard(inst: Instrument): boolean {
  return inst.instrument_role === "safeguard";
}

// Instruments to show in tables: only cause + safeguard (excludes un-classified)
const CAUSE_INSTRUMENT_TYPES = COMMON_INSTRUMENT_TYPES.filter((t) =>
  ["Flow Safety Valve", "Flow Control Valve", "Level Control Valve",
   "Pressure Control Valve", "Emergency Shutdown Valve", "Blowdown Valve",
   "Control Valve"].some((k) => t.includes(k))
);

const SAFEGUARD_TYPES = COMMON_INSTRUMENT_TYPES.filter((t) =>
  ["Pressure Switch", "Level Switch", "Pressure Safety Valve",
   "Gas Detector", "Fire Detector", "Deluge"].some((k) => t.includes(k))
);

interface EquipmentReviewTableProps {
  node: PIDNode;
  onValidated: (updatedNode: PIDNode) => void;
}

export default function EquipmentReviewTable({ node, onValidated }: EquipmentReviewTableProps) {
  const [equipment, setEquipment] = useState<Equipment[]>(node.equipment);
  const [instruments, setInstruments] = useState<Instrument[]>(node.instruments);
  const [smeName, setSmeName] = useState("");
  const [upstreamPressure, setUpstreamPressure] = useState(
    node.upstream_pressure_psig != null ? String(node.upstream_pressure_psig) : ""
  );
  const [validating, setValidating] = useState(false);
  const [message, setMessage] = useState("");

  // Add equipment form state
  const [showAddEquipment, setShowAddEquipment] = useState(false);
  const [newEqTag, setNewEqTag] = useState("");
  const [newEqName, setNewEqName] = useState("");
  const [newEqType, setNewEqType] = useState("");
  const [newEqPressure, setNewEqPressure] = useState("");
  const [newEqUpstream, setNewEqUpstream] = useState("");
  const [newEqDownstream, setNewEqDownstream] = useState("");

  // Add cause instrument form state
  const [showAddCause, setShowAddCause] = useState(false);
  const [newCauseTag, setNewCauseTag] = useState("");
  const [newCauseType, setNewCauseType] = useState("");
  const [newCauseAssocEquip, setNewCauseAssocEquip] = useState("");
  const [newCausePosition, setNewCausePosition] = useState("");
  const [newCausePhase, setNewCausePhase] = useState("");

  // Add safety device form state
  const [showAddSafeguard, setShowAddSafeguard] = useState(false);
  const [newSgTag, setNewSgTag] = useState("");
  const [newSgType, setNewSgType] = useState("");
  const [newSgAssocEquip, setNewSgAssocEquip] = useState("");
  const [newSgSetpoint, setNewSgSetpoint] = useState("");
  const [newSgPosition, setNewSgPosition] = useState("");
  const [newSgPhase, setNewSgPhase] = useState("");

  // Edit state
  const [editingEqIndex, setEditingEqIndex] = useState(-1);
  const [editEq, setEditEq] = useState<Equipment | null>(null);
  const [editingInstIndex, setEditingInstIndex] = useState(-1);
  const [editInst, setEditInst] = useState<Instrument | null>(null);

  const removeEquipment = (index: number) => {
    setEquipment((prev) => prev.filter((_, i) => i !== index));
    if (editingEqIndex === index) cancelEditEquipment();
  };

  const removeInstrument = (index: number) => {
    setInstruments((prev) => prev.filter((_, i) => i !== index));
    if (editingInstIndex === index) cancelEditInstrument();
  };

  const addEquipment = () => {
    if (!newEqTag.trim()) return;
    const upstream = newEqUpstream.trim()
      ? newEqUpstream.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean)
      : [];
    const downstream = newEqDownstream.trim()
      ? newEqDownstream.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean)
      : [];
    const newItem: Equipment = {
      tag: newEqTag.trim().toUpperCase(),
      name: newEqName.trim() || newEqTag.trim().toUpperCase(),
      equipment_type: newEqType.trim() || "Other",
      design_pressure: newEqPressure ? parseFloat(newEqPressure) : null,
      design_temperature: null,
      operating_pressure: null,
      operating_temperature: null,
      upstream_equipment: upstream,
      downstream_equipment: downstream,
    };
    setEquipment((prev) => [...prev, newItem]);
    setNewEqTag(""); setNewEqName(""); setNewEqType(""); setNewEqPressure("");
    setNewEqUpstream(""); setNewEqDownstream("");
    setShowAddEquipment(false);
  };

  const addCauseInstrument = () => {
    if (!newCauseTag.trim()) return;
    const newItem: Instrument = {
      tag: newCauseTag.trim().toUpperCase(),
      instrument_type: newCauseType.trim() || "Other",
      instrument_role: "cause",
      position: newCausePosition || null,
      line_phase: newCausePhase || null,
      setpoint: null,
      associated_equipment_tag: newCauseAssocEquip.trim() || null,
      pid_reference: null,
    };
    setInstruments((prev) => [...prev, newItem]);
    setNewCauseTag(""); setNewCauseType(""); setNewCauseAssocEquip("");
    setNewCausePosition(""); setNewCausePhase("");
    setShowAddCause(false);
  };

  const addSafeguard = () => {
    if (!newSgTag.trim()) return;
    const newItem: Instrument = {
      tag: newSgTag.trim().toUpperCase(),
      instrument_type: newSgType.trim() || "Other",
      instrument_role: "safeguard",
      position: newSgPosition || null,
      line_phase: newSgPhase || null,
      setpoint: newSgSetpoint ? parseFloat(newSgSetpoint) : null,
      associated_equipment_tag: newSgAssocEquip.trim() || null,
      pid_reference: null,
    };
    setInstruments((prev) => [...prev, newItem]);
    setNewSgTag(""); setNewSgType(""); setNewSgAssocEquip("");
    setNewSgSetpoint(""); setNewSgPosition(""); setNewSgPhase("");
    setShowAddSafeguard(false);
  };

  // --- Edit Equipment ---
  const startEditEquipment = (index: number) => {
    setEditingEqIndex(index);
    setEditEq({ ...equipment[index] });
  };

  const cancelEditEquipment = () => {
    setEditingEqIndex(-1);
    setEditEq(null);
  };

  const saveEditEquipment = () => {
    if (!editEq || editingEqIndex < 0) return;
    if (!editEq.tag.trim()) return;
    const updated: Equipment = {
      ...editEq,
      tag: editEq.tag.trim().toUpperCase(),
      name: editEq.name.trim() || editEq.tag.trim().toUpperCase(),
    };
    setEquipment((prev) => prev.map((eq, i) => (i === editingEqIndex ? updated : eq)));
    cancelEditEquipment();
  };

  // --- Edit Instrument ---
  const startEditInstrument = (index: number) => {
    setEditingInstIndex(index);
    setEditInst({ ...instruments[index] });
  };

  const cancelEditInstrument = () => {
    setEditingInstIndex(-1);
    setEditInst(null);
  };

  const saveEditInstrument = () => {
    if (!editInst || editingInstIndex < 0) return;
    if (!editInst.tag.trim()) return;
    const updated: Instrument = {
      ...editInst,
      tag: editInst.tag.trim().toUpperCase(),
      associated_equipment_tag: editInst.associated_equipment_tag?.trim() || null,
    };
    setInstruments((prev) => prev.map((inst, i) => (i === editingInstIndex ? updated : inst)));
    cancelEditInstrument();
  };

  // --- Download Excel ---
  const handleDownload = async () => {
    const workbook = new ExcelJS.Workbook();
    const sheet = workbook.addWorksheet("P&ID HAZOP Data");

    const boldFont: Partial<ExcelJS.Font> = { bold: true, size: 11 };
    const sectionFont: Partial<ExcelJS.Font> = { bold: true, size: 12 };
    const headerFill: ExcelJS.FillPattern = {
      type: "pattern", pattern: "solid", fgColor: { argb: "FFE2E8F0" },
    };
    const thinBorder: Partial<ExcelJS.Border> = { style: "thin", color: { argb: "FFD1D5DB" } };
    const allBorders: Partial<ExcelJS.Borders> = {
      top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder,
    };

    sheet.columns = [
      { width: 16 }, { width: 28 }, { width: 40 }, { width: 22 },
      { width: 22 }, { width: 16 }, { width: 16 },
    ];

    let row = 1;

    // --- Section 1: Major Equipment ---
    sheet.getRow(row).getCell(1).value = "MAJOR EQUIPMENT";
    sheet.getRow(row).getCell(1).font = sectionFont;
    row++;

    const eqHeaders = ["Tag", "Type", "Name", "Design Pressure (PSIG)", "Upstream Equipment", "Downstream Equipment"];
    const eqHeaderRow = sheet.getRow(row);
    eqHeaders.forEach((h, ci) => {
      const cell = eqHeaderRow.getCell(ci + 1);
      cell.value = h; cell.font = boldFont; cell.fill = headerFill; cell.border = allBorders;
    });
    row++;

    for (const eq of equipment) {
      const r = sheet.getRow(row);
      [eq.tag, eq.equipment_type, eq.name, eq.design_pressure ?? "",
       (eq.upstream_equipment || []).join(", "),
       (eq.downstream_equipment || []).join(", ")].forEach((v, ci) => {
        const cell = r.getCell(ci + 1);
        cell.value = v; cell.border = allBorders;
      });
      row++;
    }
    if (equipment.length === 0) { sheet.getRow(row).getCell(1).value = "(none)"; row++; }
    row++;

    // --- Section 2: Instruments (Cause) ---
    sheet.getRow(row).getCell(1).value = "INSTRUMENTS (CAUSE) — Control & Shutdown Valves";
    sheet.getRow(row).getCell(1).font = sectionFont;
    row++;

    const causeHeaders = ["Tag", "Type", "Position", "Line Phase", "Associated Equipment"];
    const causeHeaderRow = sheet.getRow(row);
    causeHeaders.forEach((h, ci) => {
      const cell = causeHeaderRow.getCell(ci + 1);
      cell.value = h; cell.font = boldFont; cell.fill = headerFill; cell.border = allBorders;
    });
    row++;

    const causes = instruments.filter(isCauseInstrument);
    for (const inst of causes) {
      const r = sheet.getRow(row);
      [inst.tag, inst.instrument_type, inst.position ?? "", inst.line_phase ?? "",
       inst.associated_equipment_tag ?? ""].forEach((v, ci) => {
        const cell = r.getCell(ci + 1);
        cell.value = v; cell.border = allBorders;
      });
      row++;
    }
    if (causes.length === 0) { sheet.getRow(row).getCell(1).value = "(none)"; row++; }
    row++;

    // --- Section 3: Safety Devices / Mitigation ---
    sheet.getRow(row).getCell(1).value = "SAFETY DEVICES / MITIGATION — PSV, PSHH, LSHH, Interlocks";
    sheet.getRow(row).getCell(1).font = sectionFont;
    row++;

    const sgHeaders = ["Tag", "Type", "Setpoint", "Position", "Line Phase", "Associated Equipment"];
    const sgHeaderRow = sheet.getRow(row);
    sgHeaders.forEach((h, ci) => {
      const cell = sgHeaderRow.getCell(ci + 1);
      cell.value = h; cell.font = boldFont; cell.fill = headerFill; cell.border = allBorders;
    });
    row++;

    const safeguards = instruments.filter(isSafeguard);
    for (const inst of safeguards) {
      const r = sheet.getRow(row);
      [inst.tag, inst.instrument_type, inst.setpoint ?? "", inst.position ?? "",
       inst.line_phase ?? "", inst.associated_equipment_tag ?? ""].forEach((v, ci) => {
        const cell = r.getCell(ci + 1);
        cell.value = v; cell.border = allBorders;
      });
      row++;
    }
    if (safeguards.length === 0) { sheet.getRow(row).getCell(1).value = "(none)"; row++; }
    row++;

    // --- Node Info ---
    sheet.getRow(row).getCell(1).value = "NODE INFO";
    sheet.getRow(row).getCell(1).font = sectionFont;
    row++;

    const nodeInfo: [string, string | number][] = [
      ["Node ID", node.node_id],
      ["Node Name", node.node_name],
      ["System", node.system],
    ];
    if (upstreamPressure) {
      nodeInfo.push(["Maximum Upstream Pressure (PSIG)", parseFloat(upstreamPressure)]);
    }
    for (const [label, value] of nodeInfo) {
      const r = sheet.getRow(row);
      r.getCell(1).value = label; r.getCell(1).font = boldFont; r.getCell(2).value = value;
      row++;
    }

    const buffer = await workbook.xlsx.writeBuffer();
    const blob = new Blob([buffer], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${node.node_id}_${node.node_name.replace(/[^a-zA-Z0-9]/g, "_")}_equipment.xlsx`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleValidate = async () => {
    if (!smeName.trim()) {
      setMessage("Please enter your name for validation");
      return;
    }
    setValidating(true);
    setMessage("");
    try {
      const pressure = upstreamPressure ? parseFloat(upstreamPressure) : null;
      await validateEquipment(node.node_id, equipment, instruments, smeName, undefined, pressure);
      setMessage("Equipment validated successfully");
      const updatedNode: PIDNode = {
        ...node,
        equipment,
        instruments,
        upstream_pressure_psig: pressure,
        validated_by: smeName,
      };
      onValidated(updatedNode);
    } catch {
      setMessage("Validation failed");
    } finally {
      setValidating(false);
    }
  };

  const causeInstruments = instruments
    .map((inst, globalIdx) => ({ inst, globalIdx }))
    .filter(({ inst }) => isCauseInstrument(inst));

  const safeguardInstruments = instruments
    .map((inst, globalIdx) => ({ inst, globalIdx }))
    .filter(({ inst }) => isSafeguard(inst));

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">
            Equipment & Instruments — {node.node_name}
          </h3>
          <p className="text-xs text-gray-500 mt-1">
            Review detected items. Edit, remove, or add entries before validating.
          </p>
        </div>
        <button
          onClick={handleDownload}
          disabled={equipment.length === 0 && instruments.length === 0}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Download equipment & instruments as Excel"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
          </svg>
          Download
        </button>
      </div>

      {/* P&ID Summary */}
      {(node.pid_summary || node.flow_description) && (
        <div className="px-4 py-3 bg-blue-50/50 border-b border-blue-100 space-y-2">
          {node.pid_summary && (
            <div>
              <span className="text-[10px] font-semibold text-blue-700 uppercase tracking-wide">P&ID Overview</span>
              <p className="text-xs text-gray-700 mt-0.5">{node.pid_summary}</p>
            </div>
          )}
          {node.flow_description && (
            <div>
              <span className="text-[10px] font-semibold text-blue-700 uppercase tracking-wide">Process Flow</span>
              <p className="text-xs text-gray-700 mt-0.5">{node.flow_description}</p>
            </div>
          )}
        </div>
      )}

      {/* ========================================================
          SECTION 1 — MAJOR EQUIPMENT
          ======================================================== */}
      <div className="px-4 py-3">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              1. Major Equipment ({equipment.length})
            </h4>
            <p className="text-[11px] text-gray-400 mt-0.5">
              Vessels, pumps, compressors, exchangers, headers, scrubbers.
            </p>
          </div>
          <button
            onClick={() => setShowAddEquipment(!showAddEquipment)}
            className="text-xs font-medium text-blue-600 hover:text-blue-800"
          >
            {showAddEquipment ? "Cancel" : "+ Add Equipment"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Tag</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Type</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Name</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Design P (PSIG)</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Upstream</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Downstream</th>
                <th className="text-right py-2 font-medium text-gray-600 text-xs">Action</th>
              </tr>
            </thead>
            <tbody>
              {equipment.map((eq, i) =>
                editingEqIndex === i && editEq ? (
                  <tr key={`edit-${i}`} className="border-b border-amber-100 bg-amber-50/30">
                    <td className="py-2 pr-2">
                      <input type="text" value={editEq.tag}
                        onChange={(e) => setEditEq({ ...editEq, tag: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" list="eq-types" value={editEq.equipment_type}
                        onChange={(e) => setEditEq({ ...editEq, equipment_type: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" placeholder="Type" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" value={editEq.name}
                        onChange={(e) => setEditEq({ ...editEq, name: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="number" value={editEq.design_pressure ?? ""}
                        onChange={(e) => setEditEq({ ...editEq, design_pressure: e.target.value ? parseFloat(e.target.value) : null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" value={(editEq.upstream_equipment || []).join(", ")}
                        onChange={(e) => setEditEq({ ...editEq, upstream_equipment: e.target.value.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean) })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                        placeholder="e.g. E-1010, HDR-1000" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" value={(editEq.downstream_equipment || []).join(", ")}
                        onChange={(e) => setEditEq({ ...editEq, downstream_equipment: e.target.value.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean) })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                        placeholder="e.g. P-1010, C-1010" />
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={saveEditEquipment} disabled={!editEq.tag.trim()}
                          className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Save</button>
                        <button onClick={cancelEditEquipment}
                          className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={eq.tag} className="border-b border-gray-50">
                    <td className="py-2 pr-3 font-mono text-xs">{eq.tag}</td>
                    <td className="py-2 pr-3">
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">{eq.equipment_type}</span>
                    </td>
                    <td className="py-2 pr-3 text-gray-700 text-xs">{eq.name}</td>
                    <td className="py-2 pr-3 text-gray-500 text-xs">{eq.design_pressure ?? "—"}</td>
                    <td className="py-2 pr-3 text-gray-500 text-xs font-mono">
                      {(eq.upstream_equipment || []).length > 0
                        ? (eq.upstream_equipment || []).join(", ")
                        : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="py-2 pr-3 text-gray-500 text-xs font-mono">
                      {(eq.downstream_equipment || []).length > 0
                        ? (eq.downstream_equipment || []).join(", ")
                        : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => startEditEquipment(i)}
                          className="text-xs text-amber-600 hover:text-amber-800">Edit</button>
                        <button onClick={() => removeEquipment(i)}
                          className="text-xs text-red-600 hover:text-red-800">Remove</button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {/* Add Equipment Inline Row */}
              {showAddEquipment && (
                <tr className="border-b border-blue-100 bg-blue-50/30">
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. V-1210" value={newEqTag}
                      onChange={(e) => setNewEqTag(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" list="eq-types" value={newEqType}
                      onChange={(e) => setNewEqType(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      placeholder="Type or select" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="Equipment name" value={newEqName}
                      onChange={(e) => setNewEqName(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="number" placeholder="PSIG" value={newEqPressure}
                      onChange={(e) => setNewEqPressure(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. HDR-1000" value={newEqUpstream}
                      onChange={(e) => setNewEqUpstream(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. P-1010" value={newEqDownstream}
                      onChange={(e) => setNewEqDownstream(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 text-right">
                    <button onClick={addEquipment} disabled={!newEqTag.trim()}
                      className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Add</button>
                  </td>
                </tr>
              )}
              {equipment.length === 0 && !showAddEquipment && (
                <tr>
                  <td colSpan={7} className="py-4 text-center text-gray-400 text-xs">No equipment detected</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ========================================================
          SECTION 2 — INSTRUMENTS (CAUSE)
          ======================================================== */}
      <div className="px-4 py-3 border-t border-gray-100">
        <div className="flex items-center justify-between mb-1">
          <div>
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              2. Instruments (Cause) ({causeInstruments.length})
            </h4>
            <p className="text-[11px] text-gray-400 mt-0.5">
              Control & shutdown valves whose failure can cause deviations — FSV, LCV, PCV, XCV, MOV, SDV, FCV, HCV.
            </p>
          </div>
          <button
            onClick={() => { setShowAddCause(!showAddCause); setShowAddSafeguard(false); }}
            className="text-xs font-medium text-purple-600 hover:text-purple-800"
          >
            {showAddCause ? "Cancel" : "+ Add Cause Instrument"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Tag</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Type</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Position</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Line</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Associated Equipment</th>
                <th className="text-right py-2 font-medium text-gray-600 text-xs">Action</th>
              </tr>
            </thead>
            <tbody>
              {causeInstruments.map(({ inst, globalIdx }) =>
                editingInstIndex === globalIdx && editInst ? (
                  <tr key={`edit-${globalIdx}`} className="border-b border-amber-100 bg-amber-50/30">
                    <td className="py-2 pr-2">
                      <input type="text" value={editInst.tag}
                        onChange={(e) => setEditInst({ ...editInst, tag: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" list="cause-types" value={editInst.instrument_type}
                        onChange={(e) => setEditInst({ ...editInst, instrument_type: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" placeholder="Type" />
                    </td>
                    <td className="py-2 pr-2">
                      <select value={editInst.position ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, position: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                        <option value="">—</option>
                        <option value="upstream">Upstream</option>
                        <option value="downstream">Downstream</option>
                      </select>
                    </td>
                    <td className="py-2 pr-2">
                      <select value={editInst.line_phase ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, line_phase: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                        <option value="">—</option>
                        <option value="gas">Gas</option>
                        <option value="liquid">Liquid</option>
                      </select>
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" value={editInst.associated_equipment_tag ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, associated_equipment_tag: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={saveEditInstrument} disabled={!editInst.tag.trim()}
                          className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Save</button>
                        <button onClick={cancelEditInstrument}
                          className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={inst.tag} className="border-b border-gray-50">
                    <td className="py-2 pr-3 font-mono text-xs">{inst.tag}</td>
                    <td className="py-2 pr-3">
                      <span className="px-2 py-0.5 bg-purple-50 text-purple-700 rounded text-xs">{inst.instrument_type}</span>
                    </td>
                    <td className="py-2 pr-3 text-xs text-gray-500 capitalize">{inst.position ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs text-gray-500 capitalize">{inst.line_phase ?? "—"}</td>
                    <td className="py-2 pr-3 text-gray-500 text-xs font-mono">{inst.associated_equipment_tag ?? "—"}</td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => startEditInstrument(globalIdx)}
                          className="text-xs text-amber-600 hover:text-amber-800">Edit</button>
                        <button onClick={() => removeInstrument(globalIdx)}
                          className="text-xs text-red-600 hover:text-red-800">Remove</button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {/* Add Cause Instrument Inline Row */}
              {showAddCause && (
                <tr className="border-b border-purple-100 bg-purple-50/30">
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. FSV-1210" value={newCauseTag}
                      onChange={(e) => setNewCauseTag(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" list="cause-types" value={newCauseType}
                      onChange={(e) => setNewCauseType(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      placeholder="Type or select" />
                  </td>
                  <td className="py-2 pr-2">
                    <select value={newCausePosition}
                      onChange={(e) => setNewCausePosition(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                      <option value="">—</option>
                      <option value="upstream">Upstream</option>
                      <option value="downstream">Downstream</option>
                    </select>
                  </td>
                  <td className="py-2 pr-2">
                    <select value={newCausePhase}
                      onChange={(e) => setNewCausePhase(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                      <option value="">—</option>
                      <option value="gas">Gas</option>
                      <option value="liquid">Liquid</option>
                    </select>
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. V-1210" value={newCauseAssocEquip}
                      onChange={(e) => setNewCauseAssocEquip(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 text-right">
                    <button onClick={addCauseInstrument} disabled={!newCauseTag.trim()}
                      className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Add</button>
                  </td>
                </tr>
              )}
              {causeInstruments.length === 0 && !showAddCause && (
                <tr>
                  <td colSpan={6} className="py-3 text-center text-gray-400 text-xs">No cause instruments detected</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ========================================================
          SECTION 3 — SAFETY DEVICES / MITIGATION
          ======================================================== */}
      <div className="px-4 py-3 border-t border-gray-100">
        <div className="flex items-center justify-between mb-1">
          <div>
            <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              3. Safety Devices / Mitigation ({safeguardInstruments.length})
            </h4>
            <p className="text-[11px] text-gray-400 mt-0.5">
              PSV, PSHH, PSLL, LSHH, LSLL, VSHH, interlocks, ESD/SDV trip devices.
            </p>
          </div>
          <button
            onClick={() => { setShowAddSafeguard(!showAddSafeguard); setShowAddCause(false); }}
            className="text-xs font-medium text-red-600 hover:text-red-800"
          >
            {showAddSafeguard ? "Cancel" : "+ Add Safety Device"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Tag</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Type</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Setpoint</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Position</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Line</th>
                <th className="text-left py-2 pr-3 font-medium text-gray-600 text-xs">Associated Equipment</th>
                <th className="text-right py-2 font-medium text-gray-600 text-xs">Action</th>
              </tr>
            </thead>
            <tbody>
              {safeguardInstruments.map(({ inst, globalIdx }) =>
                editingInstIndex === globalIdx && editInst ? (
                  <tr key={`edit-${globalIdx}`} className="border-b border-amber-100 bg-amber-50/30">
                    <td className="py-2 pr-2">
                      <input type="text" value={editInst.tag}
                        onChange={(e) => setEditInst({ ...editInst, tag: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" list="safeguard-types" value={editInst.instrument_type}
                        onChange={(e) => setEditInst({ ...editInst, instrument_type: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" placeholder="Type" />
                    </td>
                    <td className="py-2 pr-2">
                      <input type="number" value={editInst.setpoint ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, setpoint: e.target.value ? parseFloat(e.target.value) : null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded" placeholder="PSIG" />
                    </td>
                    <td className="py-2 pr-2">
                      <select value={editInst.position ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, position: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                        <option value="">—</option>
                        <option value="upstream">Upstream</option>
                        <option value="downstream">Downstream</option>
                      </select>
                    </td>
                    <td className="py-2 pr-2">
                      <select value={editInst.line_phase ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, line_phase: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                        <option value="">—</option>
                        <option value="gas">Gas</option>
                        <option value="liquid">Liquid</option>
                      </select>
                    </td>
                    <td className="py-2 pr-2">
                      <input type="text" value={editInst.associated_equipment_tag ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, associated_equipment_tag: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={saveEditInstrument} disabled={!editInst.tag.trim()}
                          className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Save</button>
                        <button onClick={cancelEditInstrument}
                          className="text-xs text-gray-500 hover:text-gray-700">Cancel</button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={inst.tag} className="border-b border-gray-50">
                    <td className="py-2 pr-3 font-mono text-xs">{inst.tag}</td>
                    <td className="py-2 pr-3">
                      <span className="px-2 py-0.5 bg-red-50 text-red-700 rounded text-xs">{inst.instrument_type}</span>
                    </td>
                    <td className="py-2 pr-3 text-xs text-gray-500">
                      {inst.setpoint != null ? `${inst.setpoint} PSIG` : "—"}
                    </td>
                    <td className="py-2 pr-3 text-xs text-gray-500 capitalize">{inst.position ?? "—"}</td>
                    <td className="py-2 pr-3 text-xs text-gray-500 capitalize">{inst.line_phase ?? "—"}</td>
                    <td className="py-2 pr-3 text-gray-500 text-xs font-mono">{inst.associated_equipment_tag ?? "—"}</td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button onClick={() => startEditInstrument(globalIdx)}
                          className="text-xs text-amber-600 hover:text-amber-800">Edit</button>
                        <button onClick={() => removeInstrument(globalIdx)}
                          className="text-xs text-red-600 hover:text-red-800">Remove</button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {/* Add Safety Device Inline Row */}
              {showAddSafeguard && (
                <tr className="border-b border-red-100 bg-red-50/30">
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. PSHH-1210" value={newSgTag}
                      onChange={(e) => setNewSgTag(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" list="safeguard-types" value={newSgType}
                      onChange={(e) => setNewSgType(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      placeholder="Type or select" />
                  </td>
                  <td className="py-2 pr-2">
                    <input type="number" placeholder="PSIG" value={newSgSetpoint}
                      onChange={(e) => setNewSgSetpoint(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded" />
                  </td>
                  <td className="py-2 pr-2">
                    <select value={newSgPosition}
                      onChange={(e) => setNewSgPosition(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                      <option value="">—</option>
                      <option value="upstream">Upstream</option>
                      <option value="downstream">Downstream</option>
                    </select>
                  </td>
                  <td className="py-2 pr-2">
                    <select value={newSgPhase}
                      onChange={(e) => setNewSgPhase(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded">
                      <option value="">—</option>
                      <option value="gas">Gas</option>
                      <option value="liquid">Liquid</option>
                    </select>
                  </td>
                  <td className="py-2 pr-2">
                    <input type="text" placeholder="e.g. V-1210" value={newSgAssocEquip}
                      onChange={(e) => setNewSgAssocEquip(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono" />
                  </td>
                  <td className="py-2 text-right">
                    <button onClick={addSafeguard} disabled={!newSgTag.trim()}
                      className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400">Add</button>
                  </td>
                </tr>
              )}
              {safeguardInstruments.length === 0 && !showAddSafeguard && (
                <tr>
                  <td colSpan={7} className="py-3 text-center text-gray-400 text-xs">No safety devices detected</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Node Design Parameters */}
      <div className="px-4 py-3 border-t border-gray-100">
        <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
          Node Design Parameters
        </h4>
        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-600 whitespace-nowrap">
            Maximum Upstream Pressure Source
          </label>
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              placeholder="e.g. 5000"
              value={upstreamPressure}
              onChange={(e) => setUpstreamPressure(e.target.value)}
              className="w-32 px-3 py-1.5 text-sm border border-gray-300 rounded font-mono"
            />
            <span className="text-xs text-gray-500">PSIG</span>
          </div>
        </div>
        <p className="text-[11px] text-gray-400 mt-1">
          Maximum pressure this node could see from upstream sources. Used for overpressure scenario analysis.
        </p>
      </div>

      {/* Validation Controls */}
      <div className="px-4 py-3 border-t border-gray-200 bg-gray-50 rounded-b-lg">
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Your name (SME)"
            value={smeName}
            onChange={(e) => setSmeName(e.target.value)}
            className="flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded"
          />
          <button
            onClick={handleValidate}
            disabled={validating || equipment.length === 0}
            className="px-4 py-1.5 text-sm font-medium text-white bg-green-600 rounded hover:bg-green-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            {validating ? "Validating..." : "Validate Equipment"}
          </button>
        </div>
        {message && (
          <p className={`mt-2 text-xs ${message.includes("failed") ? "text-red-600" : "text-green-600"}`}>
            {message}
          </p>
        )}
      </div>

      {/* Datalist suggestions for combo-box inputs */}
      <datalist id="eq-types">
        {COMMON_EQUIPMENT_TYPES.map((t) => <option key={t} value={t} />)}
      </datalist>
      <datalist id="cause-types">
        {CAUSE_INSTRUMENT_TYPES.map((t) => <option key={t} value={t} />)}
      </datalist>
      <datalist id="safeguard-types">
        {SAFEGUARD_TYPES.map((t) => <option key={t} value={t} />)}
      </datalist>
    </div>
  );
}
