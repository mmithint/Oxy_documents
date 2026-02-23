"""
Anthropic Claude Service — P&ID Extraction (Step 1 only)

Two extraction paths for PDF uploads:
  1. extract_pid_data()             — OCR text chunks → structured equipment/instruments
  2. extract_pid_data_with_vision() — PDF page images  → structured equipment/instruments

Knowledge document embedding and all HAZOP generation steps remain on Azure OpenAI.
"""

import json
import re

import anthropic

from app.core.config import get_settings

settings = get_settings()


# Shared JSON schema description used in both prompts
_JSON_SCHEMA = """{
    "node_name": "Descriptive name of the P&ID node/system",
    "system": "Parent system name (e.g. Hydrocarbon Processing Systems)",
    "description": "Brief description of what this P&ID covers",
    "drawing_number": "APC No. 4020(c) or null",
    "pid_summary": "2-3 sentence summary of the main equipment, purpose and key process conditions",
    "flow_description": "Step-by-step process flow: what streams enter, processing in each vessel, where outlet streams go. Include fluid types and flow direction.",
    "line_connectivity": [
        {
            "from_tag": "V-1210",
            "to_tag": "P-1210",
            "line_id": "6\\"-1210-A",
            "fluid_phase": "liquid",
            "pipe_size": "6-inch",
            "description": "V-1210 liquid outlet to P-1210 suction"
        }
    ],
    "control_loops": [
        {
            "loop_id": "LC-1210",
            "controlled_variable": "Level",
            "measuring_element": "LT-1210",
            "controller": "LIC-1210",
            "final_element": "LCV-1210",
            "controlled_equipment": "V-1210",
            "description": "Level control: LT-1210 measures level in V-1210, LIC-1210 controls LCV-1210"
        }
    ],
    "deviation_locations": [
        {
            "equipment_tag": "V-1210",
            "susceptible_deviations": ["High Pressure", "Low Level", "High Level"],
            "drawing_reference": "4020-001",
            "location_description": "Main separator — pressure-containing vessel with level and pressure instruments"
        }
    ],
    "equipment": [
        {
            "tag": "V-1210",
            "name": "V-1210 HP Oil Production Separator No. 2",
            "equipment_type": "Separator",
            "design_pressure": 450.0,
            "design_temperature": 200.0,
            "operating_pressure": 350.0,
            "operating_temperature": 150.0,
            "upstream_equipment": ["E-1010", "HDR-1000"],
            "downstream_equipment": ["P-1210", "C-1210"]
        }
    ],
    "instruments": [
        {
            "tag": "FSV-1210",
            "instrument_type": "Flow Safety Valve",
            "instrument_role": "cause",
            "position": "upstream",
            "line_phase": "gas",
            "setpoint": null,
            "associated_equipment_tag": "V-1210"
        },
        {
            "tag": "PSHH-1210",
            "instrument_type": "Pressure Switch High High",
            "instrument_role": "safeguard",
            "position": "downstream",
            "line_phase": "gas",
            "setpoint": 440.0,
            "associated_equipment_tag": "V-1210"
        }
    ]
}"""

_HAZOP_EXTRACTION_RULES = """HAZOP EXTRACTION RULES:
- INCLUDE Major Equipment: vessels, pumps, compressors, exchangers, headers, scrubbers, knockout drums, tanks, separators, flares, columns
- INCLUDE Instruments (Cause, instrument_role="cause"): control/shutdown valves ONLY — FSV, LCV, PCV, XCV, MOV, SDV, FCV, HCV, XV, FV, HV
- INCLUDE Safety Devices (Safeguard, instrument_role="safeguard"): PSV, PSHH, PSH, PSLL, PSL, LSHH, LSH, LSLL, LSL, VSHH, interlocks, ESD/SDV trip devices, BDV
- EXCLUDE: transmitters (PT, TT, LT, FT, DPT, AT, WT), indicators (PI, TI, LI, FI, PDI), controllers (PIC, TIC, LIC, FIC), alarms (PAH, TAH, LAH, FAH, PAHH, TAHH, LAHH, etc.)
- EXCLUDE: internal equipment details (baffles, internals, nozzles)"""

_COMMON_RULES = """Rules:
- Return ONLY valid JSON — no markdown fences, no explanation text before or after
- Extract ONLY equipment and instruments per HAZOP EXTRACTION RULES above
- Use null for numeric values you cannot determine
- Associate instruments with equipment using shared numeric suffixes (e.g., PSHH-1210 → V-1210)
- Do NOT invent tags that are not present in the source
- Pressure values are typically in PSIG, temperature in °F
- For drawing_number: look for "APC No.", "Drawing No.", "DWG No." labels. Use null if not found.
- For instrument instrument_role: "cause" for FSV/LCV/PCV/XCV/MOV/SDV/FCV/HCV/XV/FV/HV; "safeguard" for PSV/PSHH/PSLL/LSHH/LSLL/VSHH/BDV/interlocks/ESD
- For instrument position: "upstream" if on inlet/feed side, "downstream" if on outlet/discharge side
- For instrument line_phase: "gas" or "liquid" based on piping notation or equipment context
- For deviation_locations: list applicable deviations — "High Pressure", "Low Pressure", "High Level", "Low Level", "High Temperature", "Low Temperature", "No/Low Flow", "More/High Flow", "Reverse / Misdirected Flow"
- For equipment upstream_equipment/downstream_equipment: list tags of directly connected equipment via main process lines"""


def _extract_json(text: str) -> dict:
    """
    Extract JSON from Claude's response.
    Strips markdown code fences if the model wraps the output.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    return json.loads(text)


class AnthropicPIDService:
    """Claude-based P&ID extraction service (PDF uploads only)."""

    def __init__(self):
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
            )
        return self._client

    # ------------------------------------------------------------------
    # Path 1: OCR Text Extraction
    # ------------------------------------------------------------------

    async def extract_pid_data(self, chunks: list[str], source_filename: str) -> dict:
        """
        Send Azure Document Intelligence OCR text chunks to Claude and get
        back structured equipment and instrument data.
        """
        combined_text = "\n\n---CHUNK BOUNDARY---\n\n".join(chunks)

        system_prompt = f"""You are an expert oil & gas process engineer reading P&ID (Piping & Instrumentation Diagram) OCR text.

Your task is to extract ALL equipment and instruments from the OCR text. The text may be noisy due to OCR quality — use your engineering knowledge to interpret tags and names.

{_HAZOP_EXTRACTION_RULES}

For each instrument, determine:
  - instrument_role: "cause" for control/shutdown valves; "safeguard" for safety devices
  - position: "upstream" or "downstream" relative to the associated equipment
  - line_phase: "gas" or "liquid"

Return ONLY valid JSON in this exact format:
{_JSON_SCHEMA}

{_COMMON_RULES}
- Include vessel number in equipment name (e.g., "MBD-1010 HP Oil Production Separator No. 1")"""

        user_prompt = f"""Extract all equipment and instruments from this P&ID OCR text.

Source file: {source_filename}

OCR Text:
{combined_text}"""

        try:
            response = await self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0.1,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            result = _extract_json(response.content[0].text)
            print(
                f"[Claude P&ID Text] Extraction complete for {source_filename}: "
                f"{len(result.get('equipment', []))} equipment, "
                f"{len(result.get('instruments', []))} instruments"
            )
            return result
        except Exception as e:
            print(f"[Claude P&ID Text] Extraction failed: {e}")
            return {
                "node_name": "",
                "system": "Hydrocarbon Processing Systems",
                "description": "",
                "equipment": [],
                "instruments": [],
            }

    # ------------------------------------------------------------------
    # Path 2: Vision Extraction
    # ------------------------------------------------------------------

    async def extract_pid_data_with_vision(
        self,
        images_base64: list[dict],
        source_filename: str,
        ocr_hint: str | None = None,
    ) -> dict:
        """
        Send PDF page images to Claude Vision to extract equipment and
        instruments by visually reading the diagram.

        Catches tags that OCR misses:
          - Tags inside instrument bubbles/circles
          - Tags inside equipment symbols/boxes
          - Small or distorted text

        Args:
            images_base64: List of {"base64": str, "media_type": str} — one per PDF page
            source_filename: Original PDF filename for context
            ocr_hint: Optional OCR text for cross-reference (first 3000 chars)
        """
        system_prompt = f"""You are an expert oil & gas process engineer analyzing a P&ID (Piping & Instrumentation Diagram) image.

Your task: Look at the diagram carefully and extract ALL equipment and instruments you can see.

P&ID drawings use standard ISA symbols:
  - CIRCLES / BUBBLES = Instrument tags (e.g., PSHH-1210, LG-1210)
  - BOXES / RECTANGLES = Equipment (e.g., V-1210 Separator, E-1210 Heat Exchanger)
  - Lines with instrument connections show which instrument monitors which equipment

{_HAZOP_EXTRACTION_RULES}

IMPORTANT:
  - Read EVERY qualifying tag you see, even if partially visible or small
  - Tags inside circles are instruments — read the letters and numbers carefully
  - Follow piping arrows to determine upstream/downstream positions and fluid phases

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{_JSON_SCHEMA}

{_COMMON_RULES}
- For flow_description: follow the piping arrows and describe the full flow path from inlet to outlet
- For line_connectivity: follow piping lines on the diagram; only include connections where both tags are visible
- For control_loops: identify ISA control loop bubbles (measuring element → controller → final element)"""

        # Build message content
        content: list[dict] = []

        preamble = f"Source file: {source_filename}\n\nAnalyze this P&ID diagram and extract all equipment and instrument tags."
        if ocr_hint:
            preamble = (
                f"Source file: {source_filename}\n\n"
                f"OCR text extracted from this diagram (for cross-reference — some tags may be missing from OCR):\n"
                f"{ocr_hint[:3000]}"
            )
        content.append({"type": "text", "text": preamble})

        # Add each PDF page as an image block
        # PyMuPDF renders PDF pages as PNG — Claude supports image/png natively
        for img in images_base64:
            media_type = img["media_type"]
            if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = "image/png"  # PDF pages rendered by PyMuPDF are always PNG
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": img["base64"],
                },
            })

        try:
            response = await self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0.1,
                system=system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            result = _extract_json(response.content[0].text)
            print(
                f"[Claude P&ID Vision] Extraction complete for {source_filename}: "
                f"{len(result.get('equipment', []))} equipment, "
                f"{len(result.get('instruments', []))} instruments"
            )
            return result
        except Exception as e:
            print(f"[Claude P&ID Vision] Vision extraction failed: {e}")
            return {"equipment": [], "instruments": []}


# Module-level singleton
anthropic_pid_service = AnthropicPIDService()
