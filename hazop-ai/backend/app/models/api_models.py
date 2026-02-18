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


class DeviationCausesItem(BaseModel):
    """Causes for a single deviation, used in causes review step."""
    deviation_id: str
    equipment_tag: str
    deviation: str
    guideword: str
    parameter: str
    causes: list[str]


class GenerateCausesResponse(BaseModel):
    """Response with generated causes for SME review."""
    message: str
    node_id: str
    deviation_causes: list[DeviationCausesItem]


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
