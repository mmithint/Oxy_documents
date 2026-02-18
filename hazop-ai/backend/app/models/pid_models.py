"""
P&ID Models — Represent equipment, instruments, and node structure extracted from P&ID drawings.
These models are used after Document Intelligence parsing.

Equipment and instrument types are free-text strings so the LLM can extract
ANY type it finds on the P&ID.  The COMMON_* lists below are provided as
UI suggestions and ontology lookup keys — they are NOT a constraint.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ---------------------------------------------------------------------------
# Common types — used for UI dropdowns and ontology keys, NOT as constraints
# ---------------------------------------------------------------------------

COMMON_EQUIPMENT_TYPES: list[str] = [
    "Separator", "Header", "Compressor", "Pump", "Heat Exchanger",
    "Vessel", "Tank", "Scrubber", "Knockout Drum", "Flare", "Other",
]

COMMON_INSTRUMENT_TYPES: list[str] = [
    # Pressure
    "Pressure Switch High High", "Pressure Switch High Low",
    "Pressure Safety Valve", "Pressure Control Valve", "Pressure Transmitter",
    "Pressure Indicator",
    # Level
    "Level Switch High High", "Level Switch Low Low",
    "Level Control Valve", "Level Transmitter", "Level Gauge",
    # Temperature
    "Temperature Switch High High", "Temperature Switch Low Low",
    "Temperature Transmitter", "Temperature Indicator",
    # Flow
    "Flow Safety Valve", "Flow Control Valve", "Flow Transmitter",
    # Safety Systems
    "Gas Detector", "Fire Detector", "Deluge System", "Emergency Shutdown Valve",
    # Generic
    "Other",
]


class Equipment(BaseModel):
    """Single piece of equipment detected from P&ID."""
    tag: str = Field(..., description="Equipment tag number, e.g. V-1210")
    name: str = Field("", description="Equipment name, e.g. HP Oil Production Separator No. 2")
    equipment_type: str = Field(..., description="Equipment type (free-text, e.g. Separator, Vessel)")
    design_pressure: Optional[float] = Field(None, description="Design pressure in PSIG")
    design_temperature: Optional[float] = Field(None, description="Design temperature in °F")
    operating_pressure: Optional[float] = Field(None, description="Normal operating pressure in PSIG")
    operating_temperature: Optional[float] = Field(None, description="Normal operating temperature in °F")


class Instrument(BaseModel):
    """Single instrument/safety device detected from P&ID."""
    tag: str = Field(..., description="Instrument tag number, e.g. PSHH-1210")
    instrument_type: str = Field(..., description="Instrument type (free-text, e.g. Level Gauge, Pressure Transmitter)")
    setpoint: Optional[float] = Field(None, description="Setpoint value if known")
    associated_equipment_tag: Optional[str] = Field(None, description="Tag of equipment this instrument protects")
    pid_reference: Optional[str] = Field(None, description="P&ID drawing reference number")


class PIDNode(BaseModel):
    """
    A node extracted from P&ID.
    A node is a logical boundary grouping equipment, instruments, and piping
    for HAZOP analysis.
    """
    node_id: str = Field(..., description="Unique node identifier, e.g. '13'")
    node_name: str = Field(..., description="Full node name, e.g. 'HP Oil Production Header No. 2 and HP Oil Production Separator No. 2'")
    system: str = Field(default="Hydrocarbon Processing Systems", description="Parent system name")
    equipment: list[Equipment] = Field(default_factory=list, description="Equipment in this node")
    instruments: list[Instrument] = Field(default_factory=list, description="Instruments/safety devices in this node")
    pid_drawings: list[str] = Field(default_factory=list, description="Referenced P&ID drawing numbers")
    description: Optional[str] = Field(None, description="Node description or design intent")
    upstream_pressure_psig: Optional[float] = Field(None, description="Upstream pressure source in PSIG (max pressure the node could see)")


class PIDExtractionResult(BaseModel):
    """Result from Document Intelligence P&ID parsing."""
    source_file: str = Field(..., description="Original uploaded file name")
    blob_url: str = Field("", description="File URL or local path (e.g. when using local storage)")
    nodes: list[PIDNode] = Field(default_factory=list, description="Extracted nodes")
    raw_text: Optional[str] = Field(None, description="Raw OCR text for reference")
    confidence_score: Optional[float] = Field(None, description="Overall extraction confidence 0-1")
    ocr_chunks: list[str] = Field(default_factory=list, description="OCR text split into chunks sent to LLM")
    llm_raw_output: Optional[dict] = Field(None, description="Path 1: Raw JSON output from LLM text extraction")
    vision_raw_output: Optional[dict] = Field(None, description="Path 2: Raw JSON output from GPT-4 Vision extraction")
    merge_summary: Optional[dict] = Field(None, description="Summary of merged results from text + vision paths")
