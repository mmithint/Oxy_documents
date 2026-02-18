"""
HAZOP Models — Represent the full HAZOP report structure.
Deviations, causes, consequences, safeguards, risk scoring, and SME review status.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


# --- Enums ---

class DeviationParameter(str, Enum):
    PRESSURE = "Pressure"
    LEVEL = "Level"
    FLOW = "Flow"
    TEMPERATURE = "Temperature"
    COMPOSITION = "Composition"


class Guideword(str, Enum):
    HIGH = "High"
    LOW = "Low"
    NO = "No"
    MORE = "More"
    LESS = "Less"
    REVERSE = "Reverse"
    OTHER = "Other"


class DeviationType(str, Enum):
    """The 14 standard HAZOP deviation types used for ALL equipment."""
    HIGH_PRESSURE = "High Pressure"
    LOW_PRESSURE = "Low Pressure"
    HIGH_LEVEL = "High Level"
    LOW_LEVEL = "Low Level"
    HIGH_TEMPERATURE = "High Temperature"
    LOW_TEMPERATURE = "Low Temperature"
    NO_LOW_FLOW = "No/Low Flow"
    MORE_HIGH_FLOW = "More/High Flow"
    REVERSE_MISDIRECTED_FLOW = "Reverse / Misdirected Flow"
    TUBE_LEAK = "Tube Leak"
    COMPOSITION_CONTAMINATION = "Composition / Contamination"
    HUMAN_FACTORS = "Human Factors"
    PREVIOUS_INCIDENTS = "Previous Incidents / Learnings"
    OTHER = "Other"


STANDARD_DEVIATION_TYPES: list[str] = [dt.value for dt in DeviationType]


class ConsequenceCategory(str, Enum):
    PAF = "PAF"          # People at Facility
    PD_LOR = "PD/LOR"   # Property Damage / Loss of Revenue
    ECR = "ECR"          # Environmental


class PRClassification(str, Enum):
    """Protection Requirement classification for safeguards."""
    PR_1 = "PR-1"    # Automatic shutdown system (SIS/ESD)
    PR_2 = "PR-2"    # Process alarm + operator action
    PR_3 = "PR-3"    # Mechanical integrity / inspection
    PR_4 = "PR-4"    # Relief device (PSV)
    PR_5 = "PR-5"    # Fire & gas detection
    PR_20 = "PR-20"  # Monitoring / procedural safeguard
    PR_21 = "PR-21"  # Corrosion control / integrity management
    OTHER = "Other"


class MitigationType(str, Enum):
    CME = "CME"  # Critical Mitigation Element
    KME = "KME"  # Key Mitigation Element


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"


# --- Core HAZOP Data Models ---

class Safeguard(BaseModel):
    """A safeguard/mitigation protecting against a deviation."""
    instrument_tag: str = Field(..., description="Instrument tag, e.g. PSHH-1210")
    description: str = Field("", description="What this safeguard does")
    pr_classification: PRClassification = Field(..., description="Protection Requirement category")
    mitigation_type: Optional[MitigationType] = Field(None, description="CME or KME classification")
    pid_reference: Optional[str] = Field(None, description="P&ID drawing reference")
    control_category: Optional[str] = Field(None, description="Control category, e.g. 'Prevention', 'Detection', 'Mitigation'")
    cme_name: Optional[str] = Field(None, description="Full CME/KME descriptive name from knowledge docs")


class RiskScore(BaseModel):
    """Risk score for a single consequence category."""
    consequence: int = Field(..., ge=1, le=5, description="Consequence severity 1-5")
    probability: int = Field(..., ge=1, le=5, description="Probability level 1-5")
    risk_level: str = Field(..., description="Calculated risk level from matrix, e.g. 'A', 'B', 'C', 'D', 'E'")


class RiskAssessment(BaseModel):
    """Complete risk assessment across all consequence categories."""
    paf: Optional[RiskScore] = Field(None, description="People at Facility risk")
    pd_lor: Optional[RiskScore] = Field(None, description="Property Damage / Loss of Revenue risk")
    ecr: Optional[RiskScore] = Field(None, description="Environmental risk")


class Deviation(BaseModel):
    """
    A single HAZOP deviation row.
    This is the core unit of a HAZOP report.
    """
    deviation_id: Optional[str] = Field(None, description="Unique ID for this deviation record")
    node_id: str = Field(..., description="Parent node ID")
    equipment_tag: str = Field(..., description="Equipment this deviation applies to")

    # Deviation definition
    guideword: Guideword = Field(..., description="HAZOP guideword")
    parameter: DeviationParameter = Field(..., description="Process parameter")
    deviation: str = Field(..., description="Full deviation name, e.g. 'High Pressure'")

    # Cause (LLM-assisted + ontology)
    causes: list[str] = Field(default_factory=list, description="What could cause this deviation")

    # Drawing references
    drawing_references: list[str] = Field(default_factory=list, description="P&ID drawing references, e.g. ['DWG-1210-01']")

    # Consequences — split into intermediate and final impacts
    intermediate_consequences: list[str] = Field(default_factory=list, description="Immediate effects of the deviation")
    consequences: list[str] = Field(default_factory=list, description="Final impacts / worst credible outcomes (no safeguards assumed)")
    scenario_comments: Optional[str] = Field(None, description="Scenario narrative / final impact description")
    consequence_category: Optional[ConsequenceCategory] = Field(None, description="Primary consequence category")

    # Personnel Exposure Count
    pec: Optional[str] = Field(None, description="Personnel Exposure Count, e.g. '<5', '5-14', '>14'")

    # Safeguards (rule-based detection + LLM enrichment)
    safeguards: list[Safeguard] = Field(default_factory=list, description="Protection layers")

    # Current Risk (deterministic matrix lookup)
    risk: Optional[RiskAssessment] = Field(None, description="Current risk scores across categories")

    # Recommendations
    recommendations: list[str] = Field(default_factory=list, description="Actions if risk unacceptable")
    responsibility: Optional[str] = Field(None, description="Person/role responsible for implementing recommendations")

    # Planned Residual Risk (risk AFTER recommendations implemented)
    planned_residual_risk: Optional[RiskAssessment] = Field(None, description="Expected risk after recommendations are implemented")

    # Mandatory SME review flag
    requires_mandatory_sme_review: bool = Field(default=False, description="True for AI-only categories requiring explicit SME review")

    # SME Review
    status: ReviewStatus = Field(default=ReviewStatus.DRAFT, description="Current review status")
    reviewed_by: Optional[str] = Field(None, description="SME who reviewed")
    review_comments: Optional[str] = Field(None, description="SME comments")
    reviewed_at: Optional[datetime] = Field(None, description="Review timestamp")

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    generated_by: str = Field(default="ai_draft", description="'ai_draft' or 'manual'")


class HAZOPReport(BaseModel):
    """
    Complete HAZOP report for a single node.
    Contains all deviations with their causes, consequences, safeguards, and risk.
    """
    report_id: Optional[str] = Field(None, description="Unique report ID")
    node_id: str = Field(..., description="Node being analyzed")
    node_name: str = Field(..., description="Full node name")
    system: str = Field(default="Hydrocarbon Processing Systems")

    deviations: list[Deviation] = Field(default_factory=list, description="All deviation rows")

    # Report metadata
    status: ReviewStatus = Field(default=ReviewStatus.DRAFT)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)

    @property
    def total_deviations(self) -> int:
        return len(self.deviations)

    @property
    def approved_count(self) -> int:
        return sum(1 for d in self.deviations if d.status == ReviewStatus.APPROVED)

    @property
    def pending_count(self) -> int:
        return sum(1 for d in self.deviations if d.status != ReviewStatus.APPROVED)
