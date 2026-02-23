// ============================================================
// TypeScript types matching backend Pydantic models exactly
// ============================================================

// --- Types ---
// Equipment and instrument types are free-text strings.
// The COMMON_* lists below are used as UI dropdown suggestions only.

export type EquipmentType = string;
export type InstrumentType = string;

export const COMMON_EQUIPMENT_TYPES: string[] = [
  "Separator", "Header", "Compressor", "Pump", "Heat Exchanger",
  "Vessel", "Tank", "Scrubber", "Knockout Drum", "Flare", "Piping", "Other",
];

export const COMMON_INSTRUMENT_TYPES: string[] = [
  "Pressure Switch High High", "Pressure Switch High Low",
  "Pressure Safety Valve", "Pressure Control Valve", "Pressure Transmitter",
  "Pressure Indicator",
  "Level Switch High High", "Level Switch Low Low",
  "Level Control Valve", "Level Transmitter", "Level Gauge", "Level Indicator",
  "Temperature Switch High High", "Temperature Switch Low Low",
  "Temperature Transmitter", "Temperature Indicator",
  "Flow Safety Valve", "Flow Control Valve", "Flow Transmitter",
  "Gas Detector", "Fire Detector", "Deluge System", "Emergency Shutdown Valve",
  "Blowdown Valve",
  "Other",
];

export const STANDARD_DEVIATION_TYPES: string[] = [
  "High Pressure",
  "Low Pressure",
  "High Level",
  "Low Level",
  "High Temperature",
  "Low Temperature",
  "No/Low Flow",
  "More/High Flow",
  "Reverse / Misdirected Flow",
  "Tube Leak",
  "Composition / Contamination",
  "Human Factors",
  "Previous Incidents / Learnings",
  "Other",
];

export type PRClassification =
  | "PR-1" | "PR-2" | "PR-3" | "PR-4" | "PR-5"
  | "PR-20" | "PR-21" | "Other";

export type MitigationType = "CME" | "KME";

export type ReviewStatus =
  | "draft" | "pending_review" | "approved" | "rejected" | "revision_requested";

export type RiskLevel = "A" | "B" | "C" | "D" | "E";

export type ConsequenceCategory = "PAF" | "PD/LOR" | "ECR";

// --- P&ID Models ---

export interface Equipment {
  tag: string;
  name: string;
  equipment_type: EquipmentType;
  design_pressure: number | null;
  design_temperature: number | null;
  operating_pressure: number | null;
  operating_temperature: number | null;
  upstream_equipment: string[];
  downstream_equipment: string[];
}

export interface Instrument {
  tag: string;
  instrument_type: InstrumentType;
  setpoint: number | null;
  associated_equipment_tag: string | null;
  pid_reference: string | null;
  instrument_role: string | null;
  position: string | null;
  line_phase: string | null;
}

export interface LineConnection {
  from_tag: string;
  to_tag: string;
  line_id: string | null;
  fluid_phase: string | null;
  pipe_size: string | null;
  description: string | null;
}

export interface ControlLoop {
  loop_id: string | null;
  controlled_variable: string;
  measuring_element: string | null;
  controller: string | null;
  final_element: string;
  controlled_equipment: string;
  description: string | null;
}

export interface DeviationLocationMapping {
  equipment_tag: string;
  susceptible_deviations: string[];
  drawing_reference: string | null;
  location_description: string | null;
}

export interface PIDNode {
  node_id: string;
  node_name: string;
  system: string;
  equipment: Equipment[];
  instruments: Instrument[];
  pid_drawings: string[];
  drawing_number: string | null;
  description: string | null;
  upstream_pressure_psig: number | null;
  pid_summary: string | null;
  flow_description: string | null;
  line_connectivity: LineConnection[];
  control_loops: ControlLoop[];
  deviation_locations: DeviationLocationMapping[];
  validated_by?: string;
  validated_at?: string;
}

// --- HAZOP Models ---

export interface Safeguard {
  instrument_tag: string;
  description: string;
  pr_classification: PRClassification;
  mitigation_type: MitigationType | null;
  pid_reference: string | null;
  control_category: string | null;
  cme_name: string | null;
}

export interface RiskScore {
  consequence: number;
  probability: number;
  risk_level: RiskLevel;
}

export interface RiskAssessment {
  paf: RiskScore | null;
  pd_lor: RiskScore | null;
  ecr: RiskScore | null;
}

export interface Deviation {
  deviation_id: string;
  node_id: string;
  equipment_tag: string;
  guideword: string;
  parameter: string;
  deviation: string;
  causes: string[];
  drawing_references: string[];
  intermediate_consequences: string[];
  consequences: string[];
  scenario_comments: string | null;
  consequence_category: ConsequenceCategory | null;
  pec: string | null;
  safeguards: Safeguard[];
  risk: RiskAssessment | null;
  recommendations: string[];
  responsibility: string | null;
  planned_residual_risk: RiskAssessment | null;
  requires_mandatory_sme_review: boolean;
  status: ReviewStatus;
  reviewed_by: string | null;
  review_comments: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
  generated_by: string;
}

export interface HAZOPReport {
  report_id: string;
  node_id: string;
  node_name: string;
  system: string;
  deviations: Deviation[];
  status: ReviewStatus;
  created_at: string;
  updated_at: string;
  version: number;
}

// --- API Response Types ---

export interface UploadResponse {
  message: string;
  file_name: string;
  blob_url: string;
  nodes: PIDNode[];
  confidence_score: number | null;
  ocr_chunks: string[];
  llm_raw_output: Record<string, unknown> | null;
  vision_raw_output: Record<string, unknown> | null;
  merge_summary: Record<string, unknown> | null;
}

export interface HAZOPGenerateResponse {
  message: string;
  report: HAZOPReport;
}

export interface ReviewStats {
  report_id: string;
  total: number;
  approved: number;
  progress_percent: number;
  breakdown: Record<string, number>;
  is_complete: boolean;
  reviewers: string[];
}

export interface RiskLevelInfo {
  name: string;
  color: string;
  action: string;
  requires_recommendation: boolean;
}

// --- Risk level color mapping for UI ---

export const RISK_COLORS: Record<RiskLevel, string> = {
  A: "bg-green-100 text-green-800 border-green-300",
  B: "bg-yellow-100 text-yellow-800 border-yellow-300",
  C: "bg-orange-100 text-orange-800 border-orange-300",
  D: "bg-red-100 text-red-800 border-red-300",
  E: "bg-red-200 text-red-900 border-red-500",
};

export const RISK_LABELS: Record<RiskLevel, string> = {
  A: "Low",
  B: "Medium-Low",
  C: "Medium",
  D: "High",
  E: "Extreme",
};

// --- Causes Review Types ---

export interface InstrumentContext {
  tag: string;
  instrument_type: string;
  reason: string;
  pid_reference?: string | null;
}

export interface DeviationCauses {
  deviation_id: string;
  equipment_tag: string;
  deviation: string;
  guideword: string;
  parameter: string;
  causes: string[];
  pid_cause_instruments: InstrumentContext[];
  included_instruments: InstrumentContext[];
  excluded_instruments: InstrumentContext[];
}

export interface LLMContextItem {
  tag: string;
  instrument_type: string;
  reason: string;
}

export interface LLMContextSummary {
  /** All equipment — always fully provided to the LLM. */
  included_equipment: { tag: string; equipment_type: string; design_pressure: number | null }[];
  /** Control valves sent to the LLM (their failure can cause deviations). */
  included_instruments: LLMContextItem[];
  /** Transmitters, safety devices, gauges, alarms — excluded from LLM input. */
  excluded_instruments: LLMContextItem[];
  /** Maximum upstream pressure entered by SME (PSIG). */
  upstream_pressure_psig: number | null;
}

export interface GenerateCausesResponse {
  message: string;
  node_id: string;
  deviation_causes: DeviationCauses[];
  llm_context: LLMContextSummary | null;
}

// --- Consequence Review Types ---

export interface CategoryRow {
  category: "PAF" | "PD/LOR" | "ECR";
  consequences: string[];
  scenario_comments: string | null;
  current_risk: string | null;
  pec: string | null;
}

export interface OverpressureCalc {
  /** Maximum credible pressure (upstream_pressure_psig from node). */
  max_credible_pressure: number;
  /** Equipment design pressure in PSIG. */
  design_pressure: number;
  /** Ratio = max_credible / design (pure math, always present). */
  ratio: number;
  /** True when LLM confirms vessel rupture scenario from knowledge document table. */
  exceeds_2x: boolean;
  /** Hole size from Pressure Significance Table (e.g. "6-inches (150 mm)"). */
  assumed_leak_size: string | null;
  /** Significance text from table (e.g. "Stresses greater than yield strength"). */
  significance: string | null;
  /** Consequence description from table (e.g. "Potential for permanent deformation and vessel rupture"). */
  consequence_description: string | null;
  /** Document + page reference from the knowledge base. */
  source: string | null;
}

export interface DeviationConsequences {
  deviation_id: string;
  equipment_tag: string;
  deviation: string;
  guideword: string;
  parameter: string;
  causes: string[];
  pid_cause_instruments: InstrumentContext[];
  drawing_references: string[];
  intermediate_consequences: string[];
  consequences: string[];
  scenario_comments: string | null;
  consequence_category: string | null;
  /** PEC number from Production-Deck PAF table, e.g. "PEC-1", "PEC-2" */
  pec: string | null;
  /** Current risk level from table, e.g. "C5". PEC-1 → C5. */
  current_risk: string | null;
  overpressure_calc: OverpressureCalc | null;
  /** Per-category rows for table view (PAF / PD/LOR / ECR). Frontend-only field. */
  category_rows?: CategoryRow[];
}

export interface ConsequenceGenerationResponse {
  message: string;
  node_id: string;
  deviation_consequences: DeviationConsequences[];
}

// --- Safeguards Review Types ---

export interface SafeguardReview {
  instrument_tag: string;
  description: string;
  pr_classification: string;
  mitigation_type: string | null;
  pid_reference: string | null;
  control_category: string | null;
  cme_name: string | null;
  cme_id: string | null;
}

export interface DeviationSafeguards {
  deviation_id: string;
  equipment_tag: string;
  deviation: string;
  guideword: string;
  parameter: string;
  causes: string[];
  pid_cause_instruments: InstrumentContext[];
  drawing_references: string[];
  intermediate_consequences: string[];
  consequences: string[];
  scenario_comments: string | null;
  consequence_category: string | null;
  pec: string | null;
  current_risk: string | null;
  safeguards: SafeguardReview[];
  /** Auto-calculated: max(1, 5 - safeguard_count). SME-editable (1–5). */
  probability: number;
  /** Residual Risk Level from OOG matrix (A–E). Auto-calculated, SME-editable. */
  rl: string | null;
}

export interface SafeguardsGenerationResponse {
  message: string;
  node_id: string;
  deviation_safeguards: DeviationSafeguards[];
}

// --- Instrument Classification Config (Step 2 → Step 3/6) ---

export interface InstrumentClassificationConfig {
  cause_included_tags: string[];
  cause_excluded_tags: string[];
  safeguard_included_tags: string[];
  safeguard_excluded_tags: string[];
  bdv_auto_include_active: boolean;
}

export const STATUS_COLORS: Record<ReviewStatus, string> = {
  draft: "bg-gray-100 text-gray-700",
  pending_review: "bg-blue-100 text-blue-700",
  approved: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700",
  revision_requested: "bg-amber-100 text-amber-700",
};
