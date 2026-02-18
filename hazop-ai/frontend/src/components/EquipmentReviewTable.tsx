import { useState } from "react";
import ExcelJS from "exceljs";
import type { PIDNode, Equipment, Instrument } from "../types/hazop";
import { COMMON_EQUIPMENT_TYPES, COMMON_INSTRUMENT_TYPES } from "../types/hazop";
import { validateEquipment } from "../services/api";

interface EquipmentReviewTableProps {
  node: PIDNode;
  onValidated: () => void;
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

  // Add instrument form state
  const [showAddInstrument, setShowAddInstrument] = useState(false);
  const [newInstTag, setNewInstTag] = useState("");
  const [newInstType, setNewInstType] = useState("");
  const [newInstAssocEquip, setNewInstAssocEquip] = useState("");

  // Edit state — tracks which row index is being edited (-1 = none)
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
    const newItem: Equipment = {
      tag: newEqTag.trim().toUpperCase(),
      name: newEqName.trim() || newEqTag.trim().toUpperCase(),
      equipment_type: newEqType.trim() || "Other",
      design_pressure: newEqPressure ? parseFloat(newEqPressure) : null,
      design_temperature: null,
      operating_pressure: null,
      operating_temperature: null,
    };
    setEquipment((prev) => [...prev, newItem]);
    setNewEqTag("");
    setNewEqName("");
    setNewEqType("Other");
    setNewEqPressure("");
    setShowAddEquipment(false);
  };

  const addInstrument = () => {
    if (!newInstTag.trim()) return;
    const newItem: Instrument = {
      tag: newInstTag.trim().toUpperCase(),
      instrument_type: newInstType.trim() || "Other",
      setpoint: null,
      associated_equipment_tag: newInstAssocEquip.trim() || null,
      pid_reference: null,
    };
    setInstruments((prev) => [...prev, newItem]);
    setNewInstTag("");
    setNewInstType("Other");
    setNewInstAssocEquip("");
    setShowAddInstrument(false);
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
    const sheet = workbook.addWorksheet("Equipment & Instruments");

    const boldFont: Partial<ExcelJS.Font> = { bold: true, size: 11 };
    const sectionFont: Partial<ExcelJS.Font> = { bold: true, size: 12 };
    const headerFill: ExcelJS.FillPattern = {
      type: "pattern", pattern: "solid", fgColor: { argb: "FFE2E8F0" },
    };
    const thinBorder: Partial<ExcelJS.Border> = { style: "thin", color: { argb: "FFD1D5DB" } };
    const allBorders: Partial<ExcelJS.Borders> = {
      top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder,
    };

    // Column widths
    sheet.columns = [
      { width: 16 }, { width: 28 }, { width: 40 }, { width: 22 },
    ];

    let row = 1;

    // --- Equipment Section ---
    const eqTitle = sheet.getRow(row);
    eqTitle.getCell(1).value = "EQUIPMENT";
    eqTitle.getCell(1).font = sectionFont;
    row++;

    const eqHeaders = ["Tag", "Type", "Name", "Design Pressure (PSIG)"];
    const eqHeaderRow = sheet.getRow(row);
    eqHeaders.forEach((h, ci) => {
      const cell = eqHeaderRow.getCell(ci + 1);
      cell.value = h;
      cell.font = boldFont;
      cell.fill = headerFill;
      cell.border = allBorders;
    });
    row++;

    for (const eq of equipment) {
      const r = sheet.getRow(row);
      [eq.tag, eq.equipment_type, eq.name, eq.design_pressure ?? ""].forEach((v, ci) => {
        const cell = r.getCell(ci + 1);
        cell.value = v;
        cell.border = allBorders;
      });
      row++;
    }

    row++; // blank separator

    // --- Instruments Section ---
    const instTitle = sheet.getRow(row);
    instTitle.getCell(1).value = "INSTRUMENTS & SAFETY DEVICES";
    instTitle.getCell(1).font = sectionFont;
    row++;

    const instHeaders = ["Tag", "Type", "Associated Equipment"];
    const instHeaderRow = sheet.getRow(row);
    instHeaders.forEach((h, ci) => {
      const cell = instHeaderRow.getCell(ci + 1);
      cell.value = h;
      cell.font = boldFont;
      cell.fill = headerFill;
      cell.border = allBorders;
    });
    row++;

    for (const inst of instruments) {
      const r = sheet.getRow(row);
      [inst.tag, inst.instrument_type, inst.associated_equipment_tag ?? ""].forEach((v, ci) => {
        const cell = r.getCell(ci + 1);
        cell.value = v;
        cell.border = allBorders;
      });
      row++;
    }

    row++; // blank separator

    // --- Node Info ---
    const nodeTitle = sheet.getRow(row);
    nodeTitle.getCell(1).value = "NODE INFO";
    nodeTitle.getCell(1).font = sectionFont;
    row++;

    const nodeInfo: [string, string | number][] = [
      ["Node ID", node.node_id],
      ["Node Name", node.node_name],
      ["System", node.system],
    ];
    if (upstreamPressure) {
      nodeInfo.push(["Upstream Pressure (PSIG)", parseFloat(upstreamPressure)]);
    }
    for (const [label, value] of nodeInfo) {
      const r = sheet.getRow(row);
      r.getCell(1).value = label;
      r.getCell(1).font = boldFont;
      r.getCell(2).value = value;
      row++;
    }

    // Generate and download
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
      onValidated();
    } catch {
      setMessage("Validation failed");
    } finally {
      setValidating(false);
    }
  };

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

      {/* Equipment Table */}
      <div className="px-4 py-3">
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
            Equipment ({equipment.length})
          </h4>
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
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Tag</th>
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Type</th>
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Name</th>
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Design P (PSIG)</th>
                <th className="text-right py-2 font-medium text-gray-600">Action</th>
              </tr>
            </thead>
            <tbody>
              {equipment.map((eq, i) =>
                editingEqIndex === i && editEq ? (
                  <tr key={`edit-${i}`} className="border-b border-amber-100 bg-amber-50/30">
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        value={editEq.tag}
                        onChange={(e) => setEditEq({ ...editEq, tag: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        list="eq-types"
                        value={editEq.equipment_type}
                        onChange={(e) => setEditEq({ ...editEq, equipment_type: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                        placeholder="Type or select"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        value={editEq.name}
                        onChange={(e) => setEditEq({ ...editEq, name: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="number"
                        value={editEq.design_pressure ?? ""}
                        onChange={(e) => setEditEq({ ...editEq, design_pressure: e.target.value ? parseFloat(e.target.value) : null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      />
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={saveEditEquipment}
                          disabled={!editEq.tag.trim()}
                          className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400"
                        >
                          Save
                        </button>
                        <button
                          onClick={cancelEditEquipment}
                          className="text-xs text-gray-500 hover:text-gray-700"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={eq.tag} className="border-b border-gray-50">
                    <td className="py-2 pr-4 font-mono text-xs">{eq.tag}</td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">
                        {eq.equipment_type}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-gray-700">{eq.name}</td>
                    <td className="py-2 pr-4 text-gray-500">{eq.design_pressure ?? "—"}</td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => startEditEquipment(i)}
                          className="text-xs text-amber-600 hover:text-amber-800"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => removeEquipment(i)}
                          className="text-xs text-red-600 hover:text-red-800"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {/* Add Equipment Inline Row */}
              {showAddEquipment && (
                <tr className="border-b border-blue-100 bg-blue-50/30">
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      placeholder="e.g. V-1210"
                      value={newEqTag}
                      onChange={(e) => setNewEqTag(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      list="eq-types"
                      value={newEqType}
                      onChange={(e) => setNewEqType(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      placeholder="Type or select"
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      placeholder="Equipment name"
                      value={newEqName}
                      onChange={(e) => setNewEqName(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <input
                      type="number"
                      placeholder="PSIG"
                      value={newEqPressure}
                      onChange={(e) => setNewEqPressure(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                    />
                  </td>
                  <td className="py-2 text-right">
                    <button
                      onClick={addEquipment}
                      disabled={!newEqTag.trim()}
                      className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400"
                    >
                      Add
                    </button>
                  </td>
                </tr>
              )}
              {equipment.length === 0 && !showAddEquipment && (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-gray-400 text-xs">
                    No equipment detected
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Instruments Table */}
      <div className="px-4 py-3 border-t border-gray-100">
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
            Instruments & Safety Devices ({instruments.length})
          </h4>
          <button
            onClick={() => setShowAddInstrument(!showAddInstrument)}
            className="text-xs font-medium text-purple-600 hover:text-purple-800"
          >
            {showAddInstrument ? "Cancel" : "+ Add Instrument"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Tag</th>
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Type</th>
                <th className="text-left py-2 pr-4 font-medium text-gray-600">Associated Equipment</th>
                <th className="text-right py-2 font-medium text-gray-600">Action</th>
              </tr>
            </thead>
            <tbody>
              {instruments.map((inst, i) =>
                editingInstIndex === i && editInst ? (
                  <tr key={`edit-${i}`} className="border-b border-amber-100 bg-amber-50/30">
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        value={editInst.tag}
                        onChange={(e) => setEditInst({ ...editInst, tag: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        list="inst-types"
                        value={editInst.instrument_type}
                        onChange={(e) => setEditInst({ ...editInst, instrument_type: e.target.value })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                        placeholder="Type or select"
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="text"
                        value={editInst.associated_equipment_tag ?? ""}
                        onChange={(e) => setEditInst({ ...editInst, associated_equipment_tag: e.target.value || null })}
                        className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                      />
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={saveEditInstrument}
                          disabled={!editInst.tag.trim()}
                          className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400"
                        >
                          Save
                        </button>
                        <button
                          onClick={cancelEditInstrument}
                          className="text-xs text-gray-500 hover:text-gray-700"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <tr key={inst.tag} className="border-b border-gray-50">
                    <td className="py-2 pr-4 font-mono text-xs">{inst.tag}</td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 bg-purple-50 text-purple-700 rounded text-xs">
                        {inst.instrument_type}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-gray-500 text-xs">
                      {inst.associated_equipment_tag ?? "—"}
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => startEditInstrument(i)}
                          className="text-xs text-amber-600 hover:text-amber-800"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => removeInstrument(i)}
                          className="text-xs text-red-600 hover:text-red-800"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              )}
              {/* Add Instrument Inline Row */}
              {showAddInstrument && (
                <tr className="border-b border-purple-100 bg-purple-50/30">
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      placeholder="e.g. PSHH-1210"
                      value={newInstTag}
                      onChange={(e) => setNewInstTag(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      list="inst-types"
                      value={newInstType}
                      onChange={(e) => setNewInstType(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
                      placeholder="Type or select"
                    />
                  </td>
                  <td className="py-2 pr-2">
                    <input
                      type="text"
                      placeholder="e.g. V-1210"
                      value={newInstAssocEquip}
                      onChange={(e) => setNewInstAssocEquip(e.target.value)}
                      className="w-full px-2 py-1 text-xs border border-gray-300 rounded font-mono"
                    />
                  </td>
                  <td className="py-2 text-right">
                    <button
                      onClick={addInstrument}
                      disabled={!newInstTag.trim()}
                      className="text-xs font-medium text-green-600 hover:text-green-800 disabled:text-gray-400"
                    >
                      Add
                    </button>
                  </td>
                </tr>
              )}
              {instruments.length === 0 && !showAddInstrument && (
                <tr>
                  <td colSpan={4} className="py-4 text-center text-gray-400 text-xs">
                    No instruments detected
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Upstream Pressure Source */}
      <div className="px-4 py-3 border-t border-gray-100">
        <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
          Node Design Parameters
        </h4>
        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-600 whitespace-nowrap">
            Upstream Pressure Source
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
        {COMMON_EQUIPMENT_TYPES.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <datalist id="inst-types">
        {COMMON_INSTRUMENT_TYPES.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
    </div>
  );
}
