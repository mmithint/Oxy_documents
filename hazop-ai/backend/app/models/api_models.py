"""
API Request/Response Models — Used by FastAPI route endpoints.
Separate from internal domain models to maintain clean API contracts.
"""

from pydantic import BaseModel, Field
from typing import Optional
from app.models.pid_models import PIDNode, Equipment, Instrument
from app.models.hazop_models import (
    Deviation, HAZOPReport, ReviewStatus, RiskAssessment
)


# --- Upload ---

class UploadResponse(BaseModel):
    """Response after P&ID upload and parsing."""
    message: str
    file_name: str
    blob_url: str
    nodes: list[PIDNode]
    confidence_score: Optional[float] = None
    ocr_chunks: list[str] = []
    llm_raw_output: Optional[dict] = None
    vision_raw_output: Optional[dict] = None
    merge_summary: Optional[dict] = None


# --- Equipment Validation (SME Step) ---

class EquipmentValidationRequest(BaseModel):
    """SME confirms/edits detected equipment list."""
    node_id: str
    confirmed_equipment: list[Equipment]
    confirmed_instruments: list[Instrument]
    sme_name: str
    comments: Optional[str] = None
    upstream_pressure_psig: Optional[float] = None


class EquipmentValidationResponse(BaseModel):
    message: str
    node_id: str
    equipment_count: int
    instrument_count: int
    validated: bool = True


# --- HAZOP Generation ---

class HAZOPGenerateRequest(BaseModel):
    """Request to generate HAZOP for a validated node."""
    node_id: str
    include_recommendations: bool = True
    selected_deviation_types: Optional[list[str]] = Field(
        None,
        description="Deviation types to include. If null/omitted, all 14 standard types are used."
    )


class HAZOPGenerateResponse(BaseModel):
    """Response with generated HAZOP draft."""
    message: str
    report: HAZOPReport


# --- SME Review ---

class ReviewRequest(BaseModel):
    """SME review action on a deviation."""
    deviation_id: str
    action: ReviewStatus
    reviewer_name: str
    comments: Optional[str] = None


class ReviewResponse(BaseModel):
    message: str
    deviation_id: str
    new_status: ReviewStatus


# --- Deviation Edit ---

class DeviationEditRequest(BaseModel):
    """SME edits a deviation row."""
    deviation_id: str
    causes: Optional[list[str]] = None
    consequences: Optional[list[str]] = None
    recommendations: Optional[list[str]] = None
    risk_override: Optional[RiskAssessment] = None
    editor_name: str


class DeviationEditResponse(BaseModel):
    message: str
    deviation_id: str
    updated: bool = True


# --- Causes Review (Pre-Generation SME Step) ---

class GenerateCausesRequest(BaseModel):
    """Request to generate causes for SME review before full HAZOP generation."""
    node_id: str
    selected_deviation_types: Optional[list[str]] = Field(
        None,
        description="Deviation types to include. If null/omitted, all 14 standard types are used."
    )
    cause_included_tags: Optional[list[str]] = Field(
        None,
        description="SME-overridden list of instrument tags to include for cause generation. If null, default classification is used."
    )
    cause_excluded_tags: Optional[list[str]] = Field(
        None,
        description="SME-overridden list of instrument tags to exclude from cause generation. If null, default classification is used."
    )


class InstrumentContextItem(BaseModel):
    """A single instrument entry in a per-deviation instrument panel."""
    tag: str
    instrument_type: str
    reason: str
    pid_reference: Optional[str] = None


class DeviationCausesItem(BaseModel):
    """Causes for a single deviation, used in causes review step."""
    deviation_id: str
    equipment_tag: str
    deviation: str
    guideword: str
    parameter: str
    causes: list[str]
    included_instruments: list[InstrumentContextItem] = []
    excluded_instruments: list[InstrumentContextItem] = []


class LLMContextItem(BaseModel):
    """A single instrument entry in the LLM context transparency summary."""
    tag: str
    instrument_type: str
    reason: str  # Human-readable explanation of why it is included or excluded


class LLMContextSummary(BaseModel):
    """
    Transparency summary of what was and wasn't sent to the LLM for cause generation.

    Design intent:
      - Equipment is always fully provided (needed to reference tags in causes).
      - Control valves are included: their failure CAN be the root cause of a deviation.
      - Transmitters, indicators, gauges, safety devices, and alarms are excluded:
        they are either passive measurement devices or safeguards — not root causes.
      - Upstream pressure (SME-entered) and equipment pressures from the diagram
        are included as numeric context.
    """
    included_equipment: list[dict]         # All equipment always provided to LLM
    included_instruments: list[LLMContextItem]  # Control valves sent to LLM
    excluded_instruments: list[LLMContextItem]  # Transmitters/safety devices/etc. excluded
    upstream_pressure_psig: Optional[float] = None   # SME-entered max upstream pressure


class GenerateCausesResponse(BaseModel):
    """Response with generated causes for SME review."""
    message: str
    node_id: str
    deviation_causes: list[DeviationCausesItem]
    llm_context: Optional[LLMContextSummary] = None


class ApproveCausesRequest(BaseModel):
    """SME approves/edits causes before full HAZOP generation."""
    node_id: str
    sme_name: str
    deviation_causes: list[DeviationCausesItem]
    comments: Optional[str] = None


class ApproveCausesResponse(BaseModel):
    message: str
    node_id: str
    deviations_count: int
    approved: bool = True


# --- Consequence Review (Pre-Generation SME Step) ---

class OverpressureCalc(BaseModel):
    """
    Overpressure calculation for High Pressure deviations.

    The pressure ratio (max_credible / design_pressure) is computed deterministically
    in Python.  All thresholds and hole sizes are looked up from the knowledge base
    documents by the LLM — nothing beyond the ratio is hardcoded here.

    Fields populated by LLM table lookup (from Guideline for Consequence Development
    in PHA Studies, Document #60.400.301.07, Page 14):
      - assumed_leak_size       e.g. "1/4-inch (6 mm)", "3/4-inch (20 mm)", "6-inches (150 mm)"
      - significance            e.g. "Stresses greater than yield strength"
      - consequence_description e.g. "Potential for permanent deformation and vessel rupture"
      - exceeds_2x              True only when LLM confirms vessel rupture scenario
      - source                  Document + page reference from the knowledge doc
    """
    max_credible_pressure: float           # upstream_pressure_psig from node
    design_pressure: float                 # equipment.design_pressure
    ratio: float                           # max_credible / design (pure math)
    exceeds_2x: bool                       # LLM-confirmed vessel rupture flag
    assumed_leak_size: Optional[str]       # from knowledge document table
    significance: Optional[str]           # pressure significance text from document
    consequence_description: Optional[str]  # consequence text from document
    source: Optional[str]                  # document + page reference


class DeviationConsequencesItem(BaseModel):
    """Consequences for a single deviation, used in consequence review step."""
    deviation_id: str
    equipment_tag: str
    deviation: str
    guideword: str
    parameter: str
    causes: list[str]                  # from SME-approved causes
    drawing_references: list[str] = Field(default_factory=list)
    intermediate_consequences: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    scenario_comments: Optional[str] = None
    consequence_category: Optional[str] = None   # "PAF", "PD/LOR", or "ECR"
    pec: Optional[str] = None                    # PEC number from table, e.g. "PEC-1", "PEC-2"
    current_risk: Optional[str] = None           # Current risk level e.g. "C5" (PEC-1 → C5)
    overpressure_calc: Optional[OverpressureCalc] = None


class GenerateConsequencesRequest(BaseModel):
    """Request to generate consequences for SME review before full HAZOP generation."""
    node_id: str
    selected_deviation_types: Optional[list[str]] = Field(
        None,
        description="Deviation types to include. If null/omitted, all 14 standard types are used."
    )


class GenerateConsequencesResponse(BaseModel):
    """Response with generated consequences for SME review."""
    message: str
    node_id: str
    deviation_consequences: list[DeviationConsequencesItem]


class ApproveConsequencesRequest(BaseModel):
    """SME approves/edits consequences before full HAZOP generation."""
    node_id: str
    sme_name: str
    deviation_consequences: list[DeviationConsequencesItem]
    comments: Optional[str] = None


class ApproveConsequencesResponse(BaseModel):
    message: str
    node_id: str
    deviations_count: int
    approved: bool = True


# --- Safeguards Review (Pre-Generation SME Step) ---

class SafeguardReviewItem(BaseModel):
    """A single safeguard for SME review, with PR classification from HSE Risk Assessment doc."""
    instrument_tag: str              # e.g. "PSHH-1010" or "Gas Detection System"
    description: str                 # Full mitigation text (LLM-enriched)
    pr_classification: str           # e.g. "PR-1", "PR-4", "PC-4" — from HSE doc via RAG
    mitigation_type: Optional[str]   # "CME" or "KME" — from HSE doc
    pid_reference: Optional[str]     # P&ID drawing reference e.g. "4020"
    control_category: Optional[str]  # "Prevention", "Detection", "Mitigation"
    cme_name: Optional[str]          # Full CME name from HSE Risk Assessment doc
    cme_id: Optional[str]            # CME ID looked up from CME register table in knowledge docs


class DeviationSafeguardsItem(BaseModel):
    """Safeguards for a single deviation, used in safeguards review step."""
    deviation_id: str
    equipment_tag: str
    deviation: str
    guideword: str
    parameter: str
    causes: list[str]
    drawing_references: list[str] = Field(default_factory=list)
    intermediate_consequences: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)
    scenario_comments: Optional[str] = None
    consequence_category: Optional[str] = None   # "PAF", "PD/LOR", or "ECR"
    pec: Optional[str] = None                    # "PEC-1", "PEC-2", "PEC-3", "PEC-4"
    current_risk: Optional[str] = None           # e.g. "C5", "D4"
    safeguards: list[SafeguardReviewItem] = Field(default_factory=list)


class GenerateSafeguardsRequest(BaseModel):
    """Request to generate safeguards for SME review before full HAZOP generation."""
    node_id: str
    selected_deviation_types: Optional[list[str]] = Field(
        None,
        description="Deviation types to include. If null/omitted, all 14 standard types are used."
    )
    safeguard_included_tags: Optional[list[str]] = Field(
        None,
        description="SME-overridden list of instrument tags to include for safeguard generation. If null, default matching is used."
    )
    safeguard_excluded_tags: Optional[list[str]] = Field(
        None,
        description="SME-overridden list of instrument tags to exclude from safeguard generation. If null, default matching is used."
    )


class GenerateSafeguardsResponse(BaseModel):
    """Response with generated safeguards for SME review."""
    message: str
    node_id: str
    deviation_safeguards: list[DeviationSafeguardsItem]


class ApproveSafeguardsRequest(BaseModel):
    """SME approves/edits safeguards before full HAZOP generation."""
    node_id: str
    sme_name: str
    deviation_safeguards: list[DeviationSafeguardsItem]
    comments: Optional[str] = None


class ApproveSafeguardsResponse(BaseModel):
    message: str
    node_id: str
    deviations_count: int
    approved: bool = True
