import { useState, useEffect, useMemo } from "react";
import { STANDARD_DEVIATION_TYPES } from "../types/hazop";
import type { PIDNode, Instrument, InstrumentClassificationConfig } from "../types/hazop";
import { getDeviationTypes } from "../services/api";

interface DeviationSelectorProps {
  onSubmit: (selectedTypes: string[], instrumentConfig: InstrumentClassificationConfig) => void;
  onBack: () => void;
  node: PIDNode;
  initialConfig?: InstrumentClassificationConfig;
}

const AI_CATEGORIES = new Set(["Human Factors", "Previous Incidents / Learnings"]);

// ---------------------------------------------------------------------------
// Default classification tables (from client's specification)
// ---------------------------------------------------------------------------

// Prefixes whose instruments should be INCLUDED for cause generation
const CAUSE_INCLUDE_PREFIXES = new Set([
  "LCV", "FCV", "TCV", "HCV",  // Control valves
]);

// Prefixes that are conditionally included for causes IF BDV exists in the node
const BDV_CONDITIONAL_PREFIXES = new Set(["SDV", "ESV", "BDV", "XV"]);

// Everything else is excluded from cause generation by default

// Prefixes whose instruments should be INCLUDED for safeguard generation
const SAFEGUARD_INCLUDE_PREFIXES = new Set([
  // Relief devices
  "PSV", "PRV", "RD", "TRV",
  // Shutdown valves
  "SDV", "ESV", "BDV", "XV",
  // Safety trips
  "PSHH", "PSLL", "LSHH", "LSLL", "TSHH", "TSLL", "FSHH", "FSLL",
  // Alarms
  "PAH", "PAL", "LAH", "LAL", "TAH", "TAL", "FAH", "FAL",
  // General alarm prefixes
  "PA", "LA", "FA", "TA",
  // Fire/Gas detectors
  "GD", "GDS", "FD",
]);

// Prefixes explicitly excluded from safeguard generation
const SAFEGUARD_EXCLUDE_PREFIXES = new Set([
  // Control valves
  "LCV", "FCV", "PCV", "TCV", "HCV",
  // Transmitters
  "PT", "LT", "FT", "TT", "DPT", "AT", "WT",
  // Indicators/Gauges
  "PI", "LI", "FI", "TI", "LG", "PG", "FG", "TG",
]);

function getTagPrefix(tag: string): string {
  const m = tag.toUpperCase().match(/^([A-Z]+)/);
  return m ? m[1] : tag.toUpperCase();
}

function classifyInstrumentForCauses(inst: Instrument, bdvInNode: boolean): "include" | "exclude" {
  // Primary: use instrument_role if set by P&ID extraction
  if (inst.instrument_role === "cause") return "include";
  if (inst.instrument_role === "safeguard") return "exclude";
  // Fallback: prefix-based logic
  const prefix = getTagPrefix(inst.tag);
  // Control valves always included
  if (CAUSE_INCLUDE_PREFIXES.has(prefix)) return "include";
  // Check instrument_type for control valve keywords
  if (inst.instrument_type.toLowerCase().includes("control valve")) return "include";
  // BDV conditional
  if (bdvInNode && BDV_CONDITIONAL_PREFIXES.has(prefix)) return "include";
  // Everything else excluded
  return "exclude";
}

function classifyInstrumentForSafeguards(inst: Instrument): "include" | "exclude" {
  // Primary: use instrument_role if set by P&ID extraction
  if (inst.instrument_role === "safeguard") return "include";
  if (inst.instrument_role === "cause") return "exclude";
  // Fallback: prefix-based logic
  const prefix = getTagPrefix(inst.tag);
  if (SAFEGUARD_INCLUDE_PREFIXES.has(prefix)) return "include";
  // Check for specific prefixes by startsWith (for compound prefixes like PSHH matching PSH)
  for (const p of SAFEGUARD_INCLUDE_PREFIXES) {
    if (prefix.startsWith(p)) return "include";
  }
  if (SAFEGUARD_EXCLUDE_PREFIXES.has(prefix)) return "exclude";
  // Safety device keywords
  const typeLower = inst.instrument_type.toLowerCase();
  if (["safety valve", "relief valve", "shutdown valve", "blowdown valve",
       "gas detector", "fire detector", "deluge"].some(kw => typeLower.includes(kw))) {
    return "include";
  }
  // Default: exclude from safeguards
  return "exclude";
}

function getRationale(inst: Instrument, classification: "include" | "exclude", context: "cause" | "safeguard", bdvInNode: boolean): string {
  const prefix = getTagPrefix(inst.tag);
  // Primary: role-based rationale when instrument_role is set by P&ID extraction
  if (inst.instrument_role === "cause" && context === "cause") {
    return "Control valve — role assigned by P&ID extraction";
  }
  if (inst.instrument_role === "safeguard" && context === "safeguard") {
    return "Safety device — role assigned by P&ID extraction";
  }
  if (inst.instrument_role === "cause" && context === "safeguard") {
    return "Control valve — not a safeguard (role assigned by P&ID extraction)";
  }
  if (inst.instrument_role === "safeguard" && context === "cause") {
    return "Safeguard device — not a root cause (role assigned by P&ID extraction)";
  }
  if (context === "cause") {
    if (CAUSE_INCLUDE_PREFIXES.has(prefix) || inst.instrument_type.toLowerCase().includes("control valve")) {
      return "Control valve — failure (open/closed) can directly cause a deviation";
    }
    if (bdvInNode && BDV_CONDITIONAL_PREFIXES.has(prefix)) {
      return "Shutdown/blowdown valve — included because BDV detected in node";
    }
    if (classification === "exclude") {
      if (["PSV", "PRV", "SV", "RV"].includes(prefix)) return "Safety/relief valve — safeguard, not a root cause";
      if (["GD", "GDS", "FD"].includes(prefix)) return "Gas/fire detector — safeguard, not a root cause";
      if (["SDV", "ESV", "BDV", "XV"].includes(prefix)) return "ESD/shutdown valve — safeguard, not a root cause";
      if (["PT", "LT", "FT", "TT", "DPT", "AT"].includes(prefix)) return "Transmitter — passive measurement";
      if (["PI", "LI", "FI", "TI", "LG", "PG"].includes(prefix)) return "Indicator/gauge — display only";
      if (["PA", "LA", "FA", "TA"].some(p => prefix.startsWith(p))) return "Alarm — output device, not a cause";
      return "Excluded — not a potential root cause";
    }
    return "Process instrument — included as potential cause context";
  }
  // safeguard context
  if (["PSV", "PRV", "RD", "TRV"].includes(prefix)) return "Relief device — safeguard";
  if (["SDV", "ESV", "BDV", "XV"].includes(prefix)) return "Shutdown/blowdown valve — safeguard";
  if (prefix.startsWith("PSH") || prefix.startsWith("LSH") || prefix.startsWith("TSH") || prefix.startsWith("FSH") ||
      prefix.startsWith("PSL") || prefix.startsWith("LSL") || prefix.startsWith("TSL") || prefix.startsWith("FSL")) {
    return "Safety trip — safeguard";
  }
  if (["PAH", "PAL", "LAH", "LAL", "TAH", "TAL", "FAH", "FAL", "PA", "LA", "FA", "TA"].some(p => prefix.startsWith(p))) {
    return "Alarm — safeguard";
  }
  if (["GD", "GDS", "FD"].includes(prefix)) return "Fire/gas detector — safeguard";
  if (classification === "exclude") {
    if (SAFEGUARD_EXCLUDE_PREFIXES.has(prefix)) {
      if (CAUSE_INCLUDE_PREFIXES.has(prefix)) return "Control valve — not a safeguard";
      if (["PT", "LT", "FT", "TT", "DPT", "AT"].includes(prefix)) return "Transmitter — not a safeguard";
      if (["PI", "LI", "FI", "TI", "LG", "PG"].includes(prefix)) return "Indicator/gauge — not a safeguard";
    }
    return "Excluded — not a safeguard device";
  }
  return "Safety device — included as safeguard";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type ClassificationTab = "cause" | "safeguard";

export default function DeviationSelector({ onSubmit, onBack, node, initialConfig }: DeviationSelectorProps) {
  const [deviationTypes, setDeviationTypes] = useState<string[]>(STANDARD_DEVIATION_TYPES);
  const [selected, setSelected] = useState<Set<string>>(new Set<string>());
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<ClassificationTab>("cause");

  // Detect BDV in node
  const bdvInNode = useMemo(
    () => node.instruments.some(inst => getTagPrefix(inst.tag) === "BDV"),
    [node.instruments],
  );

  // Suggested deviations from Claude's P&ID analysis
  const suggestedDeviationSet = useMemo(() => {
    const s = new Set<string>();
    for (const loc of node.deviation_locations ?? []) {
      for (const dev of loc.susceptible_deviations) s.add(dev);
    }
    return s;
  }, [node.deviation_locations]);

  // Per-deviation equipment context: deviation → [{tag, description}]
  const deviationContext = useMemo(() => {
    const map = new Map<string, { tag: string; description: string }[]>();
    for (const loc of node.deviation_locations ?? []) {
      for (const dev of loc.susceptible_deviations) {
        if (!map.has(dev)) map.set(dev, []);
        map.get(dev)!.push({
          tag: loc.equipment_tag,
          description: loc.location_description ?? "",
        });
      }
    }
    return map;
  }, [node.deviation_locations]);

  // Build default classification for all instruments
  const defaultConfig = useMemo<InstrumentClassificationConfig>(() => {
    const causeIncluded: string[] = [];
    const causeExcluded: string[] = [];
    const sgIncluded: string[] = [];
    const sgExcluded: string[] = [];

    for (const inst of node.instruments) {
      if (classifyInstrumentForCauses(inst, bdvInNode) === "include") {
        causeIncluded.push(inst.tag);
      } else {
        causeExcluded.push(inst.tag);
      }
      if (classifyInstrumentForSafeguards(inst) === "include") {
        sgIncluded.push(inst.tag);
      } else {
        sgExcluded.push(inst.tag);
      }
    }

    return {
      cause_included_tags: causeIncluded,
      cause_excluded_tags: causeExcluded,
      safeguard_included_tags: sgIncluded,
      safeguard_excluded_tags: sgExcluded,
      bdv_auto_include_active: bdvInNode,
    };
  }, [node.instruments, bdvInNode]);

  // Instrument classification state — init from initialConfig or default
  const [config, setConfig] = useState<InstrumentClassificationConfig>(
    initialConfig ?? defaultConfig,
  );

  // If node changes and no initial config, reset to defaults
  useEffect(() => {
    if (!initialConfig) {
      setConfig(defaultConfig);
    }
  }, [defaultConfig, initialConfig]);

  useEffect(() => {
    getDeviationTypes()
      .then((data) => {
        setDeviationTypes(data.deviation_types);
        // Pre-select AI-suggested types if available; fall back to all selected
        if (suggestedDeviationSet.size > 0) {
          setSelected(new Set(data.deviation_types.filter(t => suggestedDeviationSet.has(t))));
        } else {
          setSelected(new Set(data.deviation_types));
        }
      })
      .catch(() => {
        // Fallback to frontend constant
      })
      .finally(() => setLoading(false));
  }, [suggestedDeviationSet]);

  const toggleType = (typeName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(typeName)) {
        next.delete(typeName);
      } else {
        next.add(typeName);
      }
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(deviationTypes));
  const selectNone = () => setSelected(new Set());
  const selectSuggested = () => {
    if (suggestedDeviationSet.size > 0) {
      setSelected(new Set(deviationTypes.filter(t => suggestedDeviationSet.has(t))));
    }
  };

  // Instrument move handlers
  const moveToExcluded = (tag: string, context: ClassificationTab) => {
    setConfig(prev => {
      if (context === "cause") {
        return {
          ...prev,
          cause_included_tags: prev.cause_included_tags.filter(t => t !== tag),
          cause_excluded_tags: [...prev.cause_excluded_tags, tag],
        };
      }
      return {
        ...prev,
        safeguard_included_tags: prev.safeguard_included_tags.filter(t => t !== tag),
        safeguard_excluded_tags: [...prev.safeguard_excluded_tags, tag],
      };
    });
  };

  const moveToIncluded = (tag: string, context: ClassificationTab) => {
    setConfig(prev => {
      if (context === "cause") {
        return {
          ...prev,
          cause_excluded_tags: prev.cause_excluded_tags.filter(t => t !== tag),
          cause_included_tags: [...prev.cause_included_tags, tag],
        };
      }
      return {
        ...prev,
        safeguard_excluded_tags: prev.safeguard_excluded_tags.filter(t => t !== tag),
        safeguard_included_tags: [...prev.safeguard_included_tags, tag],
      };
    });
  };

  const handleSubmit = () => {
    if (selected.size === 0) return;
    onSubmit(Array.from(selected), config);
  };

  // Build instrument lookup by tag
  const instrumentByTag = useMemo(() => {
    const map = new Map<string, Instrument>();
    for (const inst of node.instruments) map.set(inst.tag, inst);
    return map;
  }, [node.instruments]);

  // Current tab's included/excluded lists
  const includedTags = activeTab === "cause" ? config.cause_included_tags : config.safeguard_included_tags;
  const excludedTags = activeTab === "cause" ? config.cause_excluded_tags : config.safeguard_excluded_tags;

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-8 text-center text-gray-400 text-sm">
        Loading deviation types...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Section A: P&ID Drawing References */}
      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-900">
            P&ID Drawing References
          </h3>
        </div>
        <div className="px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            {node.pid_drawings.length > 0 ? (
              node.pid_drawings.map((dwg) => (
                <span
                  key={dwg}
                  className="inline-flex items-center px-2.5 py-1 text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200 rounded-md"
                >
                  P&ID: {dwg}
                </span>
              ))
            ) : node.drawing_number ? (
              <span className="inline-flex items-center px-2.5 py-1 text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200 rounded-md">
                Dwg: {node.drawing_number}
              </span>
            ) : (
              <span className="text-xs text-gray-400 italic">No drawing references available</span>
            )}
          </div>
          {node.pid_summary && (
            <p className="text-xs text-gray-500 mt-2 leading-relaxed">{node.pid_summary}</p>
          )}
        </div>
      </div>

      {/* Section B: Deviation Type Selection */}
      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-200">
          <h3 className="text-sm font-semibold text-gray-900">
            Select Deviation Types for HAZOP Analysis
          </h3>
          <p className="text-xs text-gray-500 mt-1">
            Choose which deviation categories to include in the report. Each selected type
            will be analyzed for ALL equipment in this node.
          </p>
        </div>

        <div className="px-4 py-3">
          {/* Select All / None controls */}
          <div className="flex items-center gap-3 mb-3">
            <button
              onClick={selectAll}
              className="text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              Select All
            </button>
            <span className="text-gray-300">|</span>
            <button
              onClick={selectNone}
              className="text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              Clear All
            </button>
            {suggestedDeviationSet.size > 0 && (
              <>
                <span className="text-gray-300">|</span>
                <button
                  onClick={selectSuggested}
                  className="text-xs text-purple-600 hover:text-purple-800 font-medium"
                >
                  ✦ AI Suggestions ({suggestedDeviationSet.size})
                </button>
              </>
            )}
            <span className="ml-auto text-xs text-gray-500">
              {selected.size} of {deviationTypes.length} selected
            </span>
          </div>

          {/* Checklist */}
          <div className="grid grid-cols-2 gap-2">
            {deviationTypes.map((typeName, index) => (
              <label
                key={typeName}
                className={`flex flex-col gap-1 p-2.5 rounded border cursor-pointer transition-colors ${
                  selected.has(typeName)
                    ? "border-blue-200 bg-blue-50/50"
                    : "border-gray-100 bg-gray-50/30 hover:bg-gray-50"
                }`}
              >
                {/* Row 1: checkbox + name + badges */}
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={selected.has(typeName)}
                    onChange={() => toggleType(typeName)}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-800">
                    {index + 1}. {typeName}
                  </span>
                  {AI_CATEGORIES.has(typeName) ? (
                    <span className="ml-auto px-1.5 py-0.5 text-[10px] font-medium bg-amber-100 text-amber-700 rounded whitespace-nowrap">
                      AI Draft - SME Review Required
                    </span>
                  ) : suggestedDeviationSet.has(typeName) ? (
                    <span className="ml-auto px-1.5 py-0.5 text-[10px] font-medium bg-purple-100 text-purple-700 rounded whitespace-nowrap">
                      ✦ AI
                    </span>
                  ) : null}
                </div>

                {/* Row 2: applicable equipment chips (only when AI suggests this deviation) */}
                {deviationContext.has(typeName) && (
                  <div className="flex flex-wrap gap-1 pl-5">
                    {deviationContext.get(typeName)!.map(({ tag, description }) => (
                      <span
                        key={tag}
                        title={description}
                        className="inline-flex items-center px-1.5 py-0.5 text-[10px] bg-gray-100 text-gray-600 rounded border border-gray-200"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </label>
            ))}
          </div>
        </div>
      </div>

      {/* Section C: Instrument Classification */}
      {node.instruments.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="px-4 py-3 border-b border-gray-200">
            <h3 className="text-sm font-semibold text-gray-900">
              Instrument Classification
            </h3>
            <p className="text-xs text-gray-500 mt-1">
              Configure which instruments are included or excluded before generation.
              Move instruments between lists to override the default classification.
            </p>
          </div>

          {/* Tab bar */}
          <div className="flex border-b border-gray-200">
            <button
              onClick={() => setActiveTab("cause")}
              className={`flex-1 py-2.5 text-xs font-medium text-center transition-colors ${
                activeTab === "cause"
                  ? "border-b-2 border-blue-500 text-blue-700 bg-blue-50/50"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
              }`}
            >
              For Cause Generation
              <span className="ml-1.5 text-[10px] font-normal">
                ({config.cause_included_tags.length} incl. / {config.cause_excluded_tags.length} excl.)
              </span>
            </button>
            <button
              onClick={() => setActiveTab("safeguard")}
              className={`flex-1 py-2.5 text-xs font-medium text-center transition-colors ${
                activeTab === "safeguard"
                  ? "border-b-2 border-blue-500 text-blue-700 bg-blue-50/50"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
              }`}
            >
              For Safeguard Generation
              <span className="ml-1.5 text-[10px] font-normal">
                ({config.safeguard_included_tags.length} incl. / {config.safeguard_excluded_tags.length} excl.)
              </span>
            </button>
          </div>

          {/* BDV auto-include banner */}
          {bdvInNode && activeTab === "cause" && (
            <div className="mx-4 mt-3 px-3 py-2 bg-amber-50 border border-amber-200 rounded-md">
              <p className="text-xs text-amber-800">
                <span className="font-semibold">BDV detected in this node.</span>{" "}
                SDV, ESV, BDV, and XV instruments have been auto-included for cause generation
                because shutdown/blowdown valve failure can be a relevant cause when BDV is present.
              </p>
            </div>
          )}

          {/* Summary counts */}
          <div className="px-4 pt-3 pb-1">
            <p className="text-xs text-gray-500">
              <span className="font-medium text-green-700">{includedTags.length} included</span>
              {", "}
              <span className="font-medium text-gray-600">{excludedTags.length} excluded</span>
              {" "}for {activeTab === "cause" ? "cause" : "safeguard"} generation
            </p>
          </div>

          {/* Two-column layout */}
          <div className="px-4 py-3">
            <div className="grid grid-cols-2 gap-4">
              {/* Left: Included */}
              <div>
                <p className="text-[11px] font-semibold text-green-700 uppercase tracking-wide mb-2 flex items-center gap-1">
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                  Included
                </p>
                <div className="space-y-1.5 max-h-64 overflow-y-auto">
                  {includedTags.length === 0 ? (
                    <p className="text-[11px] text-gray-400 italic py-2">No instruments included</p>
                  ) : (
                    includedTags.map(tag => {
                      const inst = instrumentByTag.get(tag);
                      if (!inst) return null;
                      return (
                        <div
                          key={tag}
                          className="flex items-center justify-between gap-2 px-2.5 py-1.5 bg-green-50 border border-green-100 rounded text-xs"
                        >
                          <div className="min-w-0">
                            <span className="font-mono text-green-800 font-medium">{tag}</span>
                            <span className="text-green-600 ml-1.5 text-[11px]">{inst.instrument_type}</span>
                            <p className="text-[10px] text-green-500 italic mt-0.5 truncate">
                              {getRationale(inst, "include", activeTab, bdvInNode)}
                            </p>
                          </div>
                          <button
                            onClick={() => moveToExcluded(tag, activeTab)}
                            className="flex-shrink-0 text-[11px] px-1.5 py-0.5 text-gray-500 bg-white border border-gray-200 rounded hover:bg-red-50 hover:text-red-600 hover:border-red-200 transition-colors whitespace-nowrap"
                          >
                            Exclude
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              {/* Right: Excluded */}
              <div>
                <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  Excluded
                </p>
                <div className="space-y-1.5 max-h-64 overflow-y-auto">
                  {excludedTags.length === 0 ? (
                    <p className="text-[11px] text-gray-400 italic py-2">No instruments excluded</p>
                  ) : (
                    excludedTags.map(tag => {
                      const inst = instrumentByTag.get(tag);
                      if (!inst) return null;
                      return (
                        <div
                          key={tag}
                          className="flex items-center justify-between gap-2 px-2.5 py-1.5 bg-gray-50 border border-gray-100 rounded text-xs"
                        >
                          <div className="min-w-0">
                            <span className="font-mono text-gray-600 font-medium">{tag}</span>
                            <span className="text-gray-500 ml-1.5 text-[11px]">{inst.instrument_type}</span>
                            <p className="text-[10px] text-gray-400 italic mt-0.5 truncate">
                              {getRationale(inst, "exclude", activeTab, bdvInNode)}
                            </p>
                          </div>
                          <button
                            onClick={() => moveToIncluded(tag, activeTab)}
                            className="flex-shrink-0 text-[11px] px-1.5 py-0.5 text-gray-500 bg-white border border-gray-200 rounded hover:bg-green-50 hover:text-green-700 hover:border-green-200 transition-colors whitespace-nowrap"
                          >
                            Include
                          </button>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-3 bg-gray-50 rounded-lg flex items-center justify-between">
          <button
            onClick={onBack}
            className="px-4 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-800"
          >
            Back to Validation
          </button>
          <button
            onClick={handleSubmit}
            disabled={selected.size === 0}
            className="px-4 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            Continue with {selected.size} Deviation Types
          </button>
        </div>
      </div>
    </div>
  );
}
