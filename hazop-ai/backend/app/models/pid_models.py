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


class LineConnection(BaseModel):
    """A pipe connection between two equipment/instrument tags extracted from P&ID."""
    from_tag: str = Field(..., description="Source equipment or instrument tag, e.g. V-1210")
    to_tag: str = Field(..., description="Destination equipment or instrument tag, e.g. P-1210")
    line_id: Optional[str] = Field(None, description="Line number if labelled on P&ID, e.g. '6\"-1210-A'")
    fluid_phase: Optional[str] = Field(None, description="Fluid phase: 'gas', 'liquid', 'two-phase'")
    pipe_size: Optional[str] = Field(None, description="Nominal pipe size, e.g. '6-inch'")
    description: Optional[str] = Field(None, description="Short description, e.g. 'V-1210 liquid outlet to P-1210 suction'")


class ControlLoop(BaseModel):
    """A control loop extracted from P&ID ISA bubbles."""
    loop_id: Optional[str] = Field(None, description="Loop identifier, e.g. 'LC-1210'")
    controlled_variable: str = Field(..., description="Controlled variable: Level, Pressure, Flow, Temperature")
    measuring_element: Optional[str] = Field(None, description="Transmitter tag, e.g. 'LT-1210'")
    controller: Optional[str] = Field(None, description="Controller tag, e.g. 'LIC-1210'")
    final_element: str = Field(..., description="Control valve tag, e.g. 'LCV-1210'")
    controlled_equipment: str = Field(..., description="Equipment being controlled, e.g. 'V-1210'")
    description: Optional[str] = Field(None, description="Loop description, e.g. 'LT-1210 measures level in V-1210, LIC-1210 controls LCV-1210'")


class DeviationLocation(BaseModel):
    """Maps a piece of equipment to the HAZOP deviation types applicable to it."""
    equipment_tag: str = Field(..., description="Equipment tag, e.g. 'V-1210'")
    susceptible_deviations: list[str] = Field(
        default_factory=list,
        description="Standard deviation names applicable to this equipment, e.g. ['High Pressure', 'Low Level']"
    )
    drawing_reference: Optional[str] = Field(None, description="Drawing reference where this equipment is located")
    location_description: Optional[str] = Field(None, description="Brief description of why this equipment is susceptible to these deviations")


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
    drawing_number: Optional[str] = Field(None, description="APC / drawing number extracted from title block, e.g. 'APC No. 4020'")
    description: Optional[str] = Field(None, description="Node description or design intent")
    upstream_pressure_psig: Optional[float] = Field(None, description="Upstream pressure source in PSIG (max pressure the node could see)")
    pid_summary: Optional[str] = Field(None, description="AI-generated summary of what this P&ID shows (overall process description)")
    flow_description: Optional[str] = Field(None, description="AI-generated description of the process flow: inlet streams, what happens inside, outlet streams and destinations")
    line_connectivity: list[LineConnection] = Field(default_factory=list, description="Pipe connections between equipment/instruments extracted from P&ID")
    control_loops: list[ControlLoop] = Field(default_factory=list, description="Control loops (measuring element → controller → final element) extracted from P&ID")
    deviation_locations: list[DeviationLocation] = Field(default_factory=list, description="Mapping of equipment to applicable HAZOP deviation types")


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
