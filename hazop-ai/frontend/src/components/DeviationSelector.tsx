import { useState, useEffect } from "react";
import { STANDARD_DEVIATION_TYPES } from "../types/hazop";
import { getDeviationTypes } from "../services/api";

interface DeviationSelectorProps {
  onSubmit: (selectedTypes: string[]) => void;
  onBack: () => void;
}

const AI_CATEGORIES = new Set(["Human Factors", "Previous Incidents / Learnings"]);

export default function DeviationSelector({ onSubmit, onBack }: DeviationSelectorProps) {
  const [deviationTypes, setDeviationTypes] = useState<string[]>(STANDARD_DEVIATION_TYPES);
  const [selected, setSelected] = useState<Set<string>>(new Set(STANDARD_DEVIATION_TYPES));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDeviationTypes()
      .then((data) => {
        setDeviationTypes(data.deviation_types);
        setSelected(new Set(data.deviation_types));
      })
      .catch(() => {
        // Fallback to frontend constant
      })
      .finally(() => setLoading(false));
  }, []);

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

  const handleSubmit = () => {
    if (selected.size === 0) return;
    onSubmit(Array.from(selected));
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 px-4 py-8 text-center text-gray-400 text-sm">
        Loading deviation types...
      </div>
    );
  }

  return (
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
          <span className="ml-auto text-xs text-gray-500">
            {selected.size} of {deviationTypes.length} selected
          </span>
        </div>

        {/* Checklist */}
        <div className="grid grid-cols-2 gap-2">
          {deviationTypes.map((typeName, index) => (
            <label
              key={typeName}
              className={`flex items-center gap-2 p-2.5 rounded border cursor-pointer transition-colors ${
                selected.has(typeName)
                  ? "border-blue-200 bg-blue-50/50"
                  : "border-gray-100 bg-gray-50/30 hover:bg-gray-50"
              }`}
            >
              <input
                type="checkbox"
                checked={selected.has(typeName)}
                onChange={() => toggleType(typeName)}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-sm text-gray-800">
                {index + 1}. {typeName}
              </span>
              {AI_CATEGORIES.has(typeName) && (
                <span className="ml-auto px-1.5 py-0.5 text-[10px] font-medium bg-amber-100 text-amber-700 rounded whitespace-nowrap">
                  AI Draft - SME Review Required
                </span>
              )}
            </label>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="px-4 py-3 border-t border-gray-200 bg-gray-50 rounded-b-lg flex items-center justify-between">
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
  );
}
