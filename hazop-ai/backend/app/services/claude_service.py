"""
Claude Service — Anthropic Claude API wrapper for P&ID extraction.

Mirrors the extract_pid_data / extract_pid_data_with_vision interface of
openai_service.py so both can be called interchangeably in the comparison route.

Key differences from the GPT path:
  - Uses anthropic.Anthropic (sync client) wrapped in run_in_executor for async.
  - No response_format=json_object support — uses explicit "Return ONLY valid JSON"
    in the prompt and strips markdown code fences from the response.
  - Image format uses {"source": {"type": "base64", ...}} instead of image_url.
"""

import asyncio
import json
import anthropic

from app.core.config import get_settings
from app.services.extraction_prompts import PID_OCR_SYSTEM_PROMPT, PID_VISION_SYSTEM_PROMPT

settings = get_settings()


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that Claude sometimes wraps JSON in."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence line (e.g. ```json or ```)
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else ""
        # Remove closing fence
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


class ClaudeService:
    """Anthropic Claude client for P&ID data extraction."""

    @property
    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def extract_pid_data(self, chunks: list[str], source_filename: str) -> dict:
        """
        OCR text path — identical prompts and JSON schema as openai_service.extract_pid_data().

        Args:
            chunks: List of OCR text chunks from the P&ID.
            source_filename: Original filename for context.

        Returns:
            Dict with equipment[], instruments[], node_name, system, description.
        """
        combined_text = "\n\n---CHUNK BOUNDARY---\n\n".join(chunks)
        user_prompt = (
            f"Extract all equipment and instruments from this P&ID OCR text.\n\n"
            f"Source file: {source_filename}\n\n"
            f"OCR Text:\n{combined_text}\n\n"
            "Return ONLY valid JSON matching the schema above. No explanatory text."
        )

        def _call() -> dict:
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0,
                system=PID_OCR_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = _strip_code_fences(response.content[0].text)
            print(f"[Claude] extract_pid_data complete for {source_filename}")
            return json.loads(text)

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _call)
        except Exception as e:
            print(f"[Claude] extract_pid_data error: {e}")
            return {
                "node_name": "",
                "system": "Hydrocarbon Processing Systems",
                "description": "",
                "equipment": [],
                "instruments": [],
            }

    async def extract_pid_data_with_vision(
        self,
        images_base64: list[dict],
        source_filename: str,
        ocr_hint: str | None = None,
    ) -> dict:
        """
        Vision path — sends PDF pages as PNG images to Claude.

        Args:
            images_base64: List of {"base64": str, "media_type": str} dicts.
            source_filename: Original filename for context.
            ocr_hint: Optional OCR text to cross-reference.

        Returns:
            Dict with equipment[], instruments[] (same format as extract_pid_data).
        """
        content: list[dict] = []

        # Optional OCR hint text first
        if ocr_hint:
            content.append({
                "type": "text",
                "text": (
                    f"Source file: {source_filename}\n\n"
                    f"OCR text extracted from this diagram (for cross-reference — "
                    f"some tags may be missing from OCR):\n{ocr_hint[:3000]}"
                ),
            })
        else:
            content.append({
                "type": "text",
                "text": (
                    f"Source file: {source_filename}\n\n"
                    "Analyze this P&ID diagram and extract all equipment and instrument tags."
                ),
            })

        # Add up to 5 pages (same limit as GPT path)
        for img in images_base64[:5]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],   # "image/png"
                    "data": img["base64"],
                },
            })

        content.append({
            "type": "text",
            "text": (
                f"Extract all P&ID data from these images of: {source_filename}\n"
                "Return ONLY valid JSON matching the schema above. No explanatory text."
            ),
        })

        def _call() -> dict:
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=4096,
                temperature=0,
                system=PID_VISION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            text = _strip_code_fences(response.content[0].text)
            print(f"[Claude] extract_pid_data_with_vision complete for {source_filename}")
            return json.loads(text)

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _call)
        except Exception as e:
            print(f"[Claude] extract_pid_data_with_vision error: {e}")
            return {"equipment": [], "instruments": []}


    # ------------------------------------------------------------------
    # GENERIC JSON HELPER — Used by ConsequenceAgent and MitigationAgent
    # ------------------------------------------------------------------

    async def call_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 3000,
        temperature: float = 0.2,
    ) -> dict:
        """
        Generic Claude call returning parsed JSON.
        Used by ConsequenceAgent and MitigationAgent to replace direct
        self.openai.client.chat.completions.create() calls.
        Appends JSON instruction since Claude has no response_format parameter.
        """
        full_prompt = user_prompt + "\n\nReturn ONLY valid JSON. No explanatory text, no markdown fences."

        def _call() -> dict:
            response = self.client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": full_prompt}],
            )
            return json.loads(_strip_code_fences(response.content[0].text))

        return await asyncio.get_event_loop().run_in_executor(None, _call)

    # ------------------------------------------------------------------
    # HAZOP GENERATION — Causes, severity, recommendations
    # ------------------------------------------------------------------

    async def generate_deviation_content(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        design_pressure: float | None = None,
        design_temperature: float | None = None,
        operating_pressure: float | None = None,
        upstream_pressure_psig: float | None = None,
        existing_safeguards: list[str] | None = None,
        is_special_category: bool = False,
        node_instruments: list[dict] | None = None,
        node_equipment: list[dict] | None = None,
        pid_summary: str | None = None,
        flow_description: str | None = None,
        line_connectivity: list[dict] | None = None,
        control_loops: list[dict] | None = None,
        deviation_locations: list[dict] | None = None,
        **kwargs,  # absorb extra kwargs (e.g. knowledge_context from hazop_generator)
    ) -> dict:
        from app.services.openai_service import openai_service as _oai
        system_prompt = _oai._build_system_prompt()
        user_prompt = _oai._build_deviation_content_prompt(
            equipment_type=equipment_type, equipment_tag=equipment_tag,
            deviation=deviation, design_pressure=design_pressure,
            design_temperature=design_temperature, operating_pressure=operating_pressure,
            upstream_pressure_psig=upstream_pressure_psig,
            existing_safeguards=existing_safeguards, is_special_category=is_special_category,
            node_instruments=node_instruments, node_equipment=node_equipment,
            pid_summary=pid_summary, flow_description=flow_description,
            line_connectivity=line_connectivity, control_loops=control_loops,
            deviation_locations=deviation_locations,
        )
        try:
            result = await self.call_llm_json(system_prompt, user_prompt, max_tokens=3000, temperature=0.2)
            raw_causes = result.get("causes", [])
            if is_special_category:
                result["causes"] = [c["description"] if isinstance(c, dict) else str(c) for c in raw_causes]
            else:
                valid_tags = {inst.get("tag", "").upper() for inst in (node_instruments or [])}
                result["causes"] = [
                    c["description"] for c in raw_causes
                    if isinstance(c, dict) and c.get("tag", "").upper() in valid_tags
                ]
            return result
        except Exception as e:
            print(f"[Claude] generate_deviation_content error: {e}")
            return {"causes": [], "consequences": [], "intermediate_consequences": []}

    async def generate_recommendations(
        self,
        deviation: str,
        consequences: list[str],
        risk_level: str,
        existing_safeguards: list[str],
        knowledge_context: str | None = None,
    ) -> list[str]:
        prompt = f"""You are a process safety engineer reviewing HAZOP results.
Given this HAZOP finding, generate practical, actionable recommendations.

Deviation: {deviation}
Consequences: {json.dumps(consequences)}
Current Risk Level: {risk_level}
Existing Safeguards: {json.dumps(existing_safeguards)}
{f"Reference Context: {knowledge_context}" if knowledge_context else ""}

Return JSON with format:
{{"recommendations": ["Specific actionable recommendation 1", "Specific actionable recommendation 2"]}}

Rules:
- Each recommendation must be specific and actionable
- Reference specific equipment or systems where possible
- Do not recommend safeguards that already exist
- Maximum 5 recommendations"""
        try:
            result = await self.call_llm_json("", prompt, max_tokens=1000, temperature=0.2)
            return result.get("recommendations", [])
        except Exception as e:
            print(f"[Claude] generate_recommendations error: {e}")
            return []

    async def estimate_consequence_severity(
        self,
        equipment_type: str,
        deviation: str,
        consequences: list[str],
        design_pressure: float | None = None,
        personnel_count_nearby: int | None = None,
        knowledge_context: str | None = None,
    ) -> dict:
        prompt = f"""You are a process safety engineer estimating consequence severity for HAZOP.

Equipment: {equipment_type}
Deviation: {deviation}
Consequences: {json.dumps(consequences)}
{"Design Pressure: " + str(design_pressure) + " PSIG" if design_pressure else ""}
{"Personnel nearby: " + str(personnel_count_nearby) if personnel_count_nearby else ""}
{f"Reference Context: {knowledge_context}" if knowledge_context else ""}

Return JSON:
{{
    "paf_severity": {{"value": <1-5>, "reasoning": "Brief explanation"}},
    "pd_lor_severity": {{"value": <1-5>, "reasoning": "Brief explanation"}},
    "ecr_severity": {{"value": <1-5>, "reasoning": "Brief explanation"}},
    "base_probability": {{"value": <1-5>, "reasoning": "Brief explanation of likelihood"}}
}}

Severity: 1=Negligible, 2=Minor, 3=Moderate, 4=Major, 5=Catastrophic. Be conservative."""
        try:
            return await self.call_llm_json("", prompt, max_tokens=1000, temperature=0.2)
        except Exception as e:
            print(f"[Claude] estimate_consequence_severity error: {e}")
            return {}

    # ------------------------------------------------------------------
    # DXF/DWG EXTRACTION
    # ------------------------------------------------------------------

    async def extract_pid_from_dxf_entities(
        self,
        entity_text: str,
        source_filename: str,
    ) -> dict:
        system_prompt = """You are an expert oil & gas process engineer reading structured CAD data extracted directly from a P&ID DWG file.

The input is NOT OCR text — it is exact text entities read from the DWG's internal data structure.
Each entity has:
  - The exact text string (no OCR errors — what you see is exactly what is in the drawing)
  - x, y coordinates (the entity's position on the drawing canvas)
  - Layer name (the CAD layer this entity lives on — use this to classify entity type)

Your task is to identify ALL equipment tags and instrument tags from these entities.

HOW TO USE THE DATA:
1. Layer names hint at entity type:
   - Layers containing "EQUIP", "VESSEL", "PUMP", etc. → equipment tags
   - Layers containing "INSTR", "INSTRUMENT", "TAG" → instrument tags
   - Layers containing "TEXT", "ANNO", "NOTE", "TITLE", "BORDER" → likely non-tag text
   - When layer names are ambiguous (e.g. "0", "GENERAL"), use the text pattern itself

2. Tag patterns (ISA standard):
   Equipment : V-####, D-####, E-####, P-####, C-####, K-####, T-####, HDR-####,
               FL-####, S-####, KO-#### (2-5 letters, dash, 3-5 digits)
   Instruments: PSHH-####, PSH-####, PSV-####, PCV-####, PT-####, PI-####,
                LSHH-####, LSH-####, LSLL-####, LCV-####, LT-####, LG-####,
                TSH-####, TT-####, TI-####, FCV-####, FT-####, SDV-####,
                BDV-####, GD-####, FD-####, and many others

3. Spatial proximity — use x/y coordinates to associate instruments with equipment:
   - An instrument tag near (within ~100 coordinate units of) an equipment tag
     is likely associated with that equipment
   - Shared numeric suffix is the strongest association signal
     (e.g., PSHH-1210 → V-1210)

4. The drawing_number is usually in a title block — look for entities on layers
   named "TITLEBLOCK", "TITLE", "BORDER", "FRAME", or similar, or for text matching
   patterns like "APC No.", "DWG No.", "Drawing No.", followed by alphanumerics.

HAZOP EXTRACTION RULES:
- INCLUDE Major Equipment: vessels, pumps, compressors, exchangers, headers, scrubbers, knockout drums, tanks, separators, flares, columns
- INCLUDE Instruments (Cause, instrument_role="cause"): control/shutdown valves ONLY — FSV, LCV, PCV, XCV, MOV, SDV, FCV, HCV, XV, FV, HV
- INCLUDE Safety Devices (Safeguard, instrument_role="safeguard"): PSV, PSHH, PSH, PSLL, PSL, LSHH, LSH, LSLL, LSL, VSHH, interlocks, ESD/SDV trip devices, BDV
- EXCLUDE: transmitters (PT, TT, LT, FT, DPT, AT, WT), indicators (PI, TI, LI, FI, PDI), controllers (PIC, TIC, LIC, FIC), alarms (PAH, TAH, LAH, FAH, etc.)
- Include vessel number in equipment name (e.g., "V-1210 HP Oil Production Separator No. 2")

IMPORTANT:
- Do NOT invent tags. Only report text strings that actually appear in the entity list.
- Exact text means exact — "V-1210" in the DXF is "V-1210", not "V-l210" or "V 1210".
- Non-tag text (pipe specs, notes, dimensions, revision marks, title text) should be
  ignored — focus only on equipment and instrument tags.

Return JSON in this exact format:
{
    "node_name": "Descriptive name of the P&ID node/system",
    "system": "Parent system name (e.g. Hydrocarbon Processing Systems)",
    "description": "Brief description of what this P&ID covers",
    "drawing_number": "APC No. 4020(c) or null",
    "pid_summary": "2-3 sentence summary of what this P&ID shows overall — the main equipment, its purpose, and key process conditions inferred from the tags and text",
    "flow_description": "Step-by-step description of the process flow inferred from the equipment tags and text entities: what streams enter, what processing or separation occurs in each vessel, and where the outlet streams go. Include fluid types (oil, gas, water) and flow direction.",
    "line_connectivity": [
        {
            "from_tag": "V-1210",
            "to_tag": "P-1210",
            "line_id": null,
            "fluid_phase": "liquid",
            "pipe_size": null,
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
            "description": "Level control loop on V-1210"
        }
    ],
    "deviation_locations": [
        {
            "equipment_tag": "V-1210",
            "susceptible_deviations": ["High Pressure", "Low Level", "High Level"],
            "drawing_reference": null,
            "location_description": "Pressure vessel with level and pressure instruments"
        }
    ],
    "equipment": [
        {
            "tag": "V-1210",
            "name": "V-1210 HP Oil Production Separator No. 2",
            "equipment_type": "Separator",
            "design_pressure": null,
            "design_temperature": null,
            "operating_pressure": null,
            "operating_temperature": null,
            "upstream_equipment": ["HDR-1000"],
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
            "setpoint": null,
            "associated_equipment_tag": "V-1210"
        }
    ]
}

Rules:
- Extract ONLY equipment and instruments per HAZOP EXTRACTION RULES above — exclude transmitters, indicators, controllers, alarms
- Use null for numeric values (design_pressure, setpoint, etc.) — they are rarely in the text entities; the SME will fill them in during review
- Do NOT skip tags that match HAZOP EXTRACTION RULES
- associated_equipment_tag: use shared numeric suffix or spatial proximity
- drawing_number: extract from title-block entities if identifiable, else null
- For pid_summary: describe the overall purpose of this P&ID based on the tags and any descriptive text you see.
- For flow_description: infer the process flow from equipment tag names, types, and any piping notation in the text entities.
- For line_connectivity: infer connections from equipment tag names (e.g., separator outlet → pump inlet) and any piping notation visible. Use null for line_id/pipe_size if not in the data.
- For control_loops: identify loops from transmitter (LT/PT/FT/TT), controller (LIC/PIC/FIC/TIC), control valve (LCV/PCV/FCV/TCV) tag groupings using shared numeric suffixes.
- For deviation_locations: for each equipment, list applicable standard deviation types: "High Pressure", "Low Pressure", "High Level", "Low Level", "High Temperature", "Low Temperature", "No/Low Flow", "More/High Flow", "Reverse / Misdirected Flow".
- For instrument instrument_role: "cause" for FSV/LCV/PCV/XCV/MOV/SDV/FCV/HCV/XV/FV/HV valves; "safeguard" for PSV/PSHH/PSLL/LSHH/LSLL/VSHH/BDV/interlocks/ESD devices
- For instrument position: "upstream" if on inlet/feed side, "downstream" if on outlet/discharge side of associated equipment; infer from spatial proximity and tag naming
- For instrument line_phase: "gas" or "liquid" based on equipment type, tag context, or spatial position
- For equipment upstream_equipment/downstream_equipment: infer from equipment tag types and process flow direction (e.g., header → separator → pump)"""

        user_prompt = (
            f"Extract all equipment and instruments from this DXF entity data.\n\n"
            f"Source file: {source_filename}\n\n"
            f"Entity data:\n{entity_text}\n\n"
            "Return ONLY valid JSON matching the schema above. No explanatory text."
        )
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: json.loads(_strip_code_fences(
                    self.client.messages.create(
                        model=settings.ANTHROPIC_MODEL,
                        max_tokens=4096,
                        temperature=0,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                    ).content[0].text
                ))
            )
        except Exception as e:
            print(f"[Claude] extract_pid_from_dxf_entities error: {e}")
            return {"equipment": [], "instruments": []}


claude_service = ClaudeService()
