"""
Azure OpenAI Service — LLM + Embeddings + Vision

Three responsibilities:
  1. Embeddings — Vectorize knowledge documents for Cosmos DB vector search
  2. Chat completions — Generate causes & consequences for HAZOP deviations
  3. Vision extraction — Send P&ID images to GPT-4 Vision to detect equipment
     and instruments that OCR may miss (circles, bubbles, graphical symbols)

IMPORTANT:
  - LLM is ONLY used for cause/consequence reasoning
  - LLM does NOT calculate risk scores (that's the Risk Engine)
  - LLM does NOT classify PR categories (that's the Safeguard Classifier)
  - All LLM outputs are structured JSON, never free text
"""

import json
from openai import AzureOpenAI
from app.core.config import get_settings

settings = get_settings()


class OpenAIService:
    """Azure OpenAI client for embeddings and structured HAZOP reasoning."""

    def __init__(self):
        self._client: AzureOpenAI | None = None

    @property
    def client(self) -> AzureOpenAI:
        if self._client is None:
            self._client = AzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        return self._client

    # ------------------------------------------------------------------
    # EMBEDDINGS — For knowledge document vectorization
    # ------------------------------------------------------------------

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding vector for a text chunk.
        Used for storing knowledge documents and querying them.
        """
        response = self.client.embeddings.create(
            model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            input=text,
        )
        return response.data[0].embedding

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        response = self.client.embeddings.create(
            model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            input=texts,
        )
        return [item.embedding for item in response.data]

    # ------------------------------------------------------------------
    # P&ID DATA EXTRACTION — LLM-Based Equipment & Instrument Detection
    # ------------------------------------------------------------------

    async def extract_pid_data(self, chunks: list[str], source_filename: str) -> dict:
        """
        Send OCR text chunks to the LLM and get back structured equipment
        and instrument data extracted from a P&ID drawing.

        Args:
            chunks: List of OCR text chunks from the P&ID
            source_filename: Original filename for context

        Returns:
            Dict with equipment[], instruments[], node_name, system, description
        """
        combined_text = "\n\n---CHUNK BOUNDARY---\n\n".join(chunks)

        system_prompt = """You are an expert oil & gas process engineer reading P&ID (Piping & Instrumentation Diagram) OCR text.

Your task is to extract ALL equipment and instruments from the OCR text. The text may be noisy due to OCR quality — use your engineering knowledge to interpret tags and names.

HAZOP EXTRACTION RULES:
- INCLUDE Major Equipment: vessels, pumps, compressors, exchangers, headers, scrubbers, knockout drums, tanks, separators, flares, columns
- INCLUDE Instruments (Cause, instrument_role="cause"): control/shutdown valves ONLY — FSV, LCV, PCV, XCV, MOV, SDV, FCV, HCV, XV, FV, HV
- INCLUDE Safety Devices (Safeguard, instrument_role="safeguard"): PSV, PSHH, PSH, PSLL, PSL, LSHH, LSH, LSLL, LSL, VSHH, interlocks, ESD/SDV trip devices, BDV
- EXCLUDE: transmitters (PT, TT, LT, FT, DPT, AT, WT), indicators (PI, TI, LI, FI, PDI), controllers (PIC, TIC, LIC, FIC), alarms (PAH, TAH, LAH, FAH, PAHH, TAHH, LAHH, etc.)
- EXCLUDE: internal equipment details (baffles, internals, nozzles)
- Include vessel number in equipment name (e.g., "MBD-1010 HP Oil Production Separator No. 1")

For each instrument, determine:
  - instrument_role: "cause" for control/shutdown valves (FSV/LCV/PCV/XCV/MOV/SDV/FCV/HCV/XV); "safeguard" for safety devices (PSV/PSHH/PSLL/LSHH/LSLL/VSHH/interlocks/ESD)
  - position: "upstream" or "downstream" relative to the associated equipment (based on piping flow direction)
  - line_phase: "gas" or "liquid" (from piping notation, fluid description, or context)

Return JSON in this exact format:
{
    "node_name": "Descriptive name of the P&ID node/system",
    "system": "Parent system name (e.g. Hydrocarbon Processing Systems)",
    "description": "Brief description of what this P&ID covers",
    "drawing_number": "APC No. 4020(c)",
    "pid_summary": "2-3 sentence summary of what this P&ID shows overall — the main equipment, its purpose, and key process conditions",
    "flow_description": "Step-by-step description of the process flow: what streams enter, what processing or separation occurs in each vessel, and where the outlet streams go. Include fluid types (oil, gas, water) and flow direction.",
    "line_connectivity": [
        {
            "from_tag": "V-1210",
            "to_tag": "P-1210",
            "line_id": "6\"-1210-A",
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
            "description": "Level control: LT-1210 measures level in V-1210, LIC-1210 controls LCV-1210 to maintain setpoint"
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
}

Rules:
- Extract ONLY equipment and instruments per HAZOP EXTRACTION RULES above — exclude transmitters, indicators, controllers, alarms
- Use null for numeric values you cannot determine from the text
- Associate instruments with equipment using shared numeric suffixes (e.g., PSHH-1210 → V-1210)
- Do NOT invent tags that aren't in the text
- Pressure values are typically in PSIG, temperature in °F
- For drawing_number: look in the title block for "APC No.", "Drawing No.", "DWG No.", or similar
  reference labels. Extract the full value as-is (e.g. "APC No. 4020(c)"). Use null if not found.
- For pid_summary: concise 2-3 sentences covering the main purpose and key equipment on this drawing.
- For flow_description: trace the full process path — inlet, processing steps through each vessel, and outlet destinations. Be specific about fluid phases (oil/gas/water) and routing.
- For line_connectivity: identify pipe connections between equipment using piping notation in the text. Each entry needs from_tag and to_tag. Include line_id if a line number is visible.
- For control_loops: identify control loops from instrument tags. A loop typically has a transmitter (LT/PT/FT/TT), a controller (LIC/PIC/FIC/TIC), and a control valve (LCV/PCV/FCV/TCV). Each loop must have final_element and controlled_equipment.
- For deviation_locations: for each piece of equipment, list which standard HAZOP deviation types apply. Use: "High Pressure", "Low Pressure", "High Level", "Low Level", "High Temperature", "Low Temperature", "No/Low Flow", "More/High Flow", "Reverse / Misdirected Flow".
- For instrument instrument_role: "cause" for FSV/LCV/PCV/XCV/MOV/SDV/FCV/HCV/XV/FV/HV valves; "safeguard" for PSV/PSHH/PSLL/LSHH/LSLL/VSHH/BDV/interlocks/ESD devices
- For instrument position: "upstream" if on inlet/feed side, "downstream" if on outlet/discharge side of associated equipment
- For instrument line_phase: "gas" or "liquid" based on piping notation, fluid type, or equipment context
- For equipment upstream_equipment: list tags of equipment directly feeding into this item via main process lines
- For equipment downstream_equipment: list tags of equipment this item feeds into via main process lines"""

        user_prompt = f"""Extract all equipment and instruments from this P&ID OCR text.

Source file: {source_filename}

OCR Text:
{combined_text}"""

        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=8000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            print(f"[LLM P&ID Extraction] Error: {e}")
            return {
                "node_name": "",
                "system": "Hydrocarbon Processing Systems",
                "description": "",
                "equipment": [],
                "instruments": [],
            }

    # ------------------------------------------------------------------
    # P&ID DXF EXTRACTION — Direct CAD Entity Extraction (DWG uploads)
    # ------------------------------------------------------------------

    async def extract_pid_from_dxf_entities(
        self,
        entity_text: str,
        source_filename: str,
    ) -> dict:
        """
        Extract equipment and instruments from DXF text entities.

        Unlike extract_pid_data() which works on noisy OCR text, this method
        receives structured CAD data: text strings with x/y coordinates and
        layer names, extracted directly from the DWG file.  The text is exact —
        no OCR errors, no image quality concerns.

        Args:
            entity_text : Output of dxf_extractor.format_entities_for_llm()
                          — entities grouped by layer, each with coordinates.
            source_filename : Original DWG/DXF filename for context.

        Returns:
            Dict matching the extract_pid_data() schema:
            {node_name, system, description, drawing_number, equipment[], instruments[]}
        """
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

        user_prompt = f"""Extract all equipment and instrument tags from this DXF entity data.

Source file: {source_filename}

DXF Text Entities (grouped by CAD layer, with x/y coordinates):
{entity_text}"""

        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=8000,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            print(
                f"[DXF LLM] Extraction complete for {source_filename}: "
                f"{len(result.get('equipment', []))} equipment, "
                f"{len(result.get('instruments', []))} instruments"
            )
            return result
        except Exception as exc:
            print(f"[DXF LLM] Extraction failed: {exc}")
            return {
                "node_name": "",
                "system": "Hydrocarbon Processing Systems",
                "description": "",
                "drawing_number": None,
                "equipment": [],
                "instruments": [],
            }

    # ------------------------------------------------------------------
    # P&ID VISION EXTRACTION — Image-Based Equipment & Instrument Detection
    # ------------------------------------------------------------------

    async def extract_pid_data_with_vision(
        self,
        images_base64: list[dict],
        source_filename: str,
        ocr_hint: str | None = None,
    ) -> dict:
        """
        Send P&ID page images directly to GPT-4 Vision to extract equipment
        and instruments by *seeing* the diagram — not just reading OCR text.

        This catches items that OCR misses:
          - Tags inside circles/bubbles (ISA instrument balloons)
          - Tags inside boxes (equipment symbols)
          - Small or distorted text
          - Graphical symbols with embedded labels

        Args:
            images_base64: List of dicts with {"base64": str, "media_type": str}
                           One entry per page/image.
            source_filename: Original filename for context.
            ocr_hint: Optional OCR text to help the model cross-reference.

        Returns:
            Dict with equipment[], instruments[] (same format as extract_pid_data)
        """
        system_prompt = """You are an expert oil & gas process engineer analyzing a P&ID (Piping & Instrumentation Diagram) image.

Your task: Look at the diagram carefully and extract ALL equipment and instruments you can see.

P&ID drawings use standard ISA symbols:
  - CIRCLES / BUBBLES = Instrument tags (e.g., PSHH-1210, LT-1210, LG-1210, PT-1210)
  - BOXES / RECTANGLES = Equipment (e.g., V-1210 Separator, E-1210 Heat Exchanger)
  - DIAMONDS = Computer/logic functions
  - Lines with instrument connections show which instrument monitors which equipment

HAZOP EXTRACTION RULES (apply to what you see in the image):
  - INCLUDE Major Equipment: vessels, pumps, compressors, exchangers, headers, scrubbers, knockout drums, tanks, separators, flares, columns
  - INCLUDE Instruments (Cause, instrument_role="cause"): control/shutdown valves ONLY — FSV, LCV, PCV, XCV, MOV, SDV, FCV, HCV, XV, FV, HV (circles with these prefixes)
  - INCLUDE Safety Devices (Safeguard, instrument_role="safeguard"): PSV, PSHH, PSH, PSLL, PSL, LSHH, LSH, LSLL, LSL, VSHH, interlocks, ESD/SDV trip devices, BDV
  - EXCLUDE: transmitters (PT, TT, LT, FT, DPT, AT), indicators (PI, TI, LI, FI), controllers (PIC, TIC, LIC, FIC), alarms (PAH, TAH, LAH, FAH, etc.)
  - Include vessel number in equipment name (e.g., "V-1210 HP Oil Production Separator No. 2")

IMPORTANT:
  - Read EVERY qualifying tag you see, even if partially visible or small
  - Tags inside circles are instruments — read the letters and numbers carefully
  - Tags inside or near boxes/vessels are equipment
  - Only include instruments that match HAZOP EXTRACTION RULES above

Return JSON in this exact format:
{
    "pid_summary": "2-3 sentence summary of what this P&ID shows overall — the main equipment, its purpose, and key process conditions visible on the diagram",
    "flow_description": "Step-by-step description of the process flow visible in this diagram: what streams enter (from where), what happens inside each vessel, and where the outlet streams go. Include fluid phases (oil, gas, water) and flow direction as shown by the piping arrows.",
    "line_connectivity": [
        {
            "from_tag": "V-1210",
            "to_tag": "P-1210",
            "line_id": "6\"-1210-A",
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
            "description": "Level control: LT-1210 measures level in V-1210, LIC-1210 controls LCV-1210 to maintain setpoint"
        }
    ],
    "deviation_locations": [
        {
            "equipment_tag": "V-1210",
            "susceptible_deviations": ["High Pressure", "Low Level", "High Level"],
            "drawing_reference": "4020-001",
            "location_description": "Main separator — contains high-pressure gas and liquid phases, susceptible to overpressure and level excursions"
        }
    ],
    "equipment": [
        {
            "tag": "V-1210",
            "name": "V-1210 HP Oil Production Separator",
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
- Extract ONLY tags visible in the image that match the HAZOP EXTRACTION RULES
- Associate instruments with their connected equipment using visual connections or shared numeric suffixes
- Use null for values you cannot read from the image
- Do NOT invent tags — only report what you actually see
- If text is partially readable, include your best interpretation
- For pid_summary: describe what you see on this P&ID — the main vessels, their purpose, and overall process.
- For flow_description: follow the piping arrows and describe the full flow path from inlet to outlet, naming each vessel and the fluid type at each stage.
- For line_connectivity: follow the piping lines (arrowed pipes) on the diagram. For each visible pipe connection between two identifiable tags, record from_tag → to_tag, the line_id label if shown, the fluid phase (gas/liquid/two-phase as visible from notes or symbols), and a short description. Only include connections where both from_tag and to_tag are visible.
- For control_loops: identify ISA control loop bubbles. For each loop, find the measuring element (LT/PT/FT/TT-xxx), the controller bubble (LIC/PIC/FIC/TIC-xxx), and the final control element (LCV/PCV/FCV/TCV-xxx). Note the controlled equipment and the controlled variable (Level, Pressure, Flow, Temperature).
- For deviation_locations: for each piece of equipment, identify which HAZOP deviation types apply based on the visible instruments, fluid types, and equipment function. Use standard names: "High Pressure", "Low Pressure", "High Level", "Low Level", "High Temperature", "Low Temperature", "No/Low Flow", "More/High Flow", "Reverse / Misdirected Flow".
- For instrument instrument_role: "cause" for FSV/LCV/PCV/XCV/MOV/SDV/FCV/HCV/XV visible as valve symbols; "safeguard" for PSV/PSHH/PSLL/LSHH/LSLL/VSHH/BDV/interlock symbols
- For instrument position: "upstream" if visually on inlet/feed side of equipment, "downstream" if on outlet/discharge side
- For instrument line_phase: "gas" if on gas line (upper connections), "liquid" if on liquid line (lower connections) — infer from visual position and piping labels
- For equipment upstream_equipment/downstream_equipment: read the piping arrows to determine which equipment feeds into (upstream) and receives from (downstream) each major piece of equipment"""

        # Build message content with images
        content: list[dict] = []

        # Add OCR hint as text reference if available
        if ocr_hint:
            content.append({
                "type": "text",
                "text": f"Source file: {source_filename}\n\nOCR text extracted from this diagram (for cross-reference — some tags may be missing from OCR):\n{ocr_hint[:3000]}",
            })
        else:
            content.append({
                "type": "text",
                "text": f"Source file: {source_filename}\n\nAnalyze this P&ID diagram and extract all equipment and instrument tags.",
            })

        # Add each page image
        for img in images_base64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['media_type']};base64,{img['base64']}",
                    "detail": "high",  # High detail for reading small tags
                },
            })

        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=8000,
                response_format={"type": "json_object"},
            )

            result_content = response.choices[0].message.content
            print(f"[P&ID Vision] Extraction complete for {source_filename}")
            return json.loads(result_content)
        except Exception as e:
            print(f"[P&ID Vision] Vision extraction failed: {e}")
            return {"equipment": [], "instruments": []}

    # ------------------------------------------------------------------
    # CAUSE / CONSEQUENCE GENERATION — LLM-Assisted HAZOP Reasoning
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
    ) -> dict:
        """
        Generate full HAZOP deviation content: causes, consequences,
        intermediate consequences, scenario, mitigations, PEC,
        recommendations, responsibility, planned residual risk.

        Uses a single expanded LLM call with structured JSON output.
        Causes are grounded ONLY in P&ID information (equipment, instruments,
        line connectivity, control loops) — no external knowledge base used.

        Args:
            equipment_type: Type of equipment (e.g., "Separator")
            equipment_tag: Equipment tag (e.g., "V-1210")
            deviation: Deviation name (e.g., "High Pressure")
            design_pressure: Design pressure if known
            design_temperature: Design temperature if known
            existing_safeguards: List of safeguard descriptions already detected
            is_special_category: True for Human Factors / Previous Incidents
            line_connectivity: Pipe connections from P&ID extraction
            control_loops: Control loops from P&ID extraction
            deviation_locations: Deviation-to-equipment mapping from P&ID

        Returns:
            Dict with all HAZOP fields
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_deviation_content_prompt(
            equipment_type=equipment_type,
            equipment_tag=equipment_tag,
            deviation=deviation,
            design_pressure=design_pressure,
            design_temperature=design_temperature,
            operating_pressure=operating_pressure,
            upstream_pressure_psig=upstream_pressure_psig,
            existing_safeguards=existing_safeguards,
            is_special_category=is_special_category,
            node_instruments=node_instruments,
            node_equipment=node_equipment,
            pid_summary=pid_summary,
            flow_description=flow_description,
            line_connectivity=line_connectivity,
            control_loops=control_loops,
            deviation_locations=deviation_locations,
        )

        response = self.client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=6000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = json.loads(content)

        # Validate and flatten causes from structured {description, tag} objects
        # to plain strings, filtering out any hallucinated or invalid tags.
        raw_causes = result.get("causes", [])

        if is_special_category:
            # Human Factors / Previous Incidents: extract descriptions as-is (no tag check)
            result["causes"] = [
                c["description"] if isinstance(c, dict) else str(c)
                for c in raw_causes
            ]
        else:
            # Standard deviations: keep only causes whose tag is in the valid instrument list
            valid_tags = {
                inst.get("tag", "").upper()
                for inst in (node_instruments or [])
            }
            result["causes"] = [
                c["description"]
                for c in raw_causes
                if isinstance(c, dict)
                and c.get("tag", "").upper() in valid_tags
            ]

        return result

    async def generate_consequence_content(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        design_pressure: float | None = None,
        operating_pressure: float | None = None,
        upstream_pressure_psig: float | None = None,
        design_temperature: float | None = None,
        approved_causes: list[str] | None = None,
        pressure_ratio: float | None = None,
        overpressure_table_context: str | None = None,
        pec_table_context: str | None = None,
        knowledge_context: str | None = None,
        is_special_category: bool = False,
        pid_instruments: list[dict] | None = None,
        pid_summary: str | None = None,
        flow_description: str | None = None,
    ) -> dict:
        """
        Generate HAZOP consequence fields ONLY — causes are already SME-approved.

        Focused LLM call that generates:
          - intermediate_consequences, consequences, scenario_comments
          - consequence_category (PAF / PD/LOR / ECR)
          - personnel_exposure (PEC) using Production Deck PAF table
          - mitigation_details (including LOC + Jet Fire triggered safeguards)
          - drawing_references

        Includes overpressure calculation context so the LLM knows whether
        a vessel rupture / 6-inch leak assumption applies.

        Returns dict — same field names as generate_deviation_content() for
        compatibility with hazop_generator._enrich_all_fields().
        """
        prompt = self._build_consequence_prompt(
            equipment_type=equipment_type,
            equipment_tag=equipment_tag,
            deviation=deviation,
            design_pressure=design_pressure,
            operating_pressure=operating_pressure,
            upstream_pressure_psig=upstream_pressure_psig,
            design_temperature=design_temperature,
            approved_causes=approved_causes,
            pressure_ratio=pressure_ratio,
            overpressure_table_context=overpressure_table_context,
            pec_table_context=pec_table_context,
            knowledge_context=knowledge_context,
            is_special_category=is_special_category,
            pid_instruments=pid_instruments,
            pid_summary=pid_summary,
            flow_description=flow_description,
        )

        system_prompt = """You are a senior process safety engineer generating HAZOP consequence
analysis for a specific deviation. The causes have already been reviewed and approved by an SME.
Your task is to determine:
1. Intermediate consequences (the chain of physical effects before final impact)
2. Final consequences (worst credible outcomes, assuming NO safeguards)
3. Scenario narrative (cause → intermediate → final)
4. Consequence category (PAF, PD/LOR, or ECR)
5. Personnel Exposure Count (PEC) using the Production Deck PAF Consequence table
6. Mitigation details — including mandatory facility safeguards for LOC and Jet Fire scenarios

Rules:
- Consequences assume NO safeguards (worst credible case)
- Use knowledge context documents to ground your analysis
- If the scenario involves loss of containment or hydrocarbon release, ALWAYS add gas detection safeguard
- If the scenario involves jet fire, ALWAYS add deluge safeguard
- All outputs must be structured JSON"""

        response = self.client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
                ],
                max_completion_tokens=6000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        return json.loads(content)

    def _build_consequence_prompt(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        design_pressure: float | None,
        operating_pressure: float | None,
        upstream_pressure_psig: float | None,
        design_temperature: float | None,
        approved_causes: list[str] | None,
        pressure_ratio: float | None,
        overpressure_table_context: str | None,
        pec_table_context: str | None,
        knowledge_context: str | None,
        is_special_category: bool,
        pid_instruments: list[dict] | None,
        pid_summary: str | None = None,
        flow_description: str | None = None,
    ) -> str:
        prompt = f"""Generate HAZOP consequence analysis for this deviation.
Causes have already been approved by the SME — focus on consequences only.

Equipment Type: {equipment_type}
Equipment Tag: {equipment_tag}
Deviation: {deviation}
"""
        if pid_summary:
            prompt += f"\nP&ID Overview: {pid_summary}\n"
        if flow_description:
            prompt += f"Process Flow: {flow_description}\n"
        if design_pressure:
            prompt += f"Design Pressure: {design_pressure} PSIG\n"
        if operating_pressure:
            prompt += f"Normal Operating Pressure: {operating_pressure} PSIG\n"
        if upstream_pressure_psig:
            prompt += f"Maximum Credible Pressure (upstream blocked-flow): {upstream_pressure_psig} PSIG\n"
        if design_temperature:
            prompt += f"Design Temperature: {design_temperature} °F\n"

        if approved_causes:
            prompt += "\nSME-Approved Causes:\n"
            for c in approved_causes:
                prompt += f"  - {c}\n"

        # Overpressure table lookup — ratio is calculated in Python; thresholds come from RAG
        if pressure_ratio is not None:
            prompt += f"""
OVERPRESSURE CALCULATION:
  Maximum Credible Pressure (upstream): {upstream_pressure_psig} PSIG
  Equipment Design Pressure: {design_pressure} PSIG
  Calculated Ratio: {pressure_ratio:.2f}× (max_credible ÷ design_pressure)

"""
            if overpressure_table_context:
                prompt += (
                    "Pressure Significance Table (from company knowledge documents — "
                    "use this table to determine the correct hole size, significance text, "
                    "and consequence description for the calculated ratio above):\n"
                    f"{overpressure_table_context}\n\n"
                )
            else:
                prompt += (
                    "The pressure significance table was not retrieved from the knowledge base. "
                    "Use your engineering knowledge to determine the appropriate consequence "
                    "based on the calculated ratio.\n\n"
                )
            prompt += (
                "Look up the calculated ratio in the Pressure Significance Table above. "
                "Return the matching hole_size, significance, consequence_description, "
                "is_vessel_rupture (true only for vessel rupture row), and the document source "
                "in the overpressure_result field. "
                "If the ratio indicates vessel rupture, you MUST include vessel rupture and "
                "the corresponding leak size in intermediate_consequences and consequences, "
                "and include jet fire as an escalation consequence in scenario_comments.\n"
            )

        if pid_instruments:
            prompt += (
                "\nP&ID Instruments on this equipment (for consequence context — "
                "these are safeguards and instruments present on the P&ID; assume they "
                "may fail or be unavailable when assessing worst-credible consequences):\n"
            )
            for inst in pid_instruments:
                tag = inst.get("tag", "")
                itype = inst.get("instrument_type", "")
                pid_ref = inst.get("pid_reference", "")
                line = f"  - {tag} ({itype})"
                if inst.get("position"):
                    line += f", {inst['position']}"
                if inst.get("line_phase"):
                    line += f", {inst['line_phase']} line"
                if pid_ref:
                    line += f" [P&ID: {pid_ref}]"
                prompt += line + "\n"
        else:
            prompt += "\nNo instruments found on P&ID for this equipment.\n"

        # PEC table lookup — pressure (x-axis) × hole size (y-axis) → PEC number
        system_pressure = upstream_pressure_psig or design_pressure
        prompt += f"\n--- PEC LOOKUP (Personnel Exposure Count) ---\n"
        prompt += f"  System pressure from P&ID diagram: {system_pressure} PSIG\n"
        if pressure_ratio is not None:
            prompt += (
                "  Hole size: use the hole_size value you determine in overpressure_result "
                "(from the Pressure Significance Table above).\n"
            )
        else:
            prompt += "  Hole size: estimate from deviation severity and consequence type.\n"

        if pec_table_context:
            prompt += (
                "\nProduction-Deck PAF Consequence Table (from knowledge documents — "
                "x-axis = pressure in PSIG rows, y-axis = hole size in inches columns):\n"
                f"{pec_table_context}\n\n"
                "Lookup steps:\n"
                f"  1. Find the row matching the system pressure ({system_pressure} PSIG)\n"
                "  2. Find the column matching the hole size\n"
                "  3. Cell at intersection = PEC number (e.g., PEC-1, PEC-2, PEC-3)\n"
                "  4. Determine current_risk from the table for that PEC:\n"
                "       PEC-1 → current_risk = 'C5'\n"
                "       All other PEC values: read the current risk from the same table row.\n"
            )
        else:
            prompt += (
                "The PEC table was not retrieved from the knowledge base. "
                "Estimate PEC from the scenario severity and consequence type.\n"
                "If PEC-1: current_risk = 'C5'. Otherwise estimate from risk matrix.\n"
            )

        if knowledge_context:
            prompt += (
                f"\nRelevant Knowledge Context (from company documents — "
                f"use this to ground your analysis):\n{knowledge_context}\n"
            )

        prompt += """
MANDATORY SAFEGUARD RULES:
- If any consequence involves Loss of Containment (LOC), hydrocarbon release, pressurized leak,
  or vessel rupture: you MUST include in mitigation_details:
    name: "Gas detection (2 detectors at 20% LEL or 1 at 45% LEL — triggers closure of BSDV
           and XV on each subsea flowline, and SSV and SDV on each dry tree well)"
    control_category: "Detection", cme_kme: "CME"
- If any consequence involves Jet Fire: you MUST include in mitigation_details:
    name: "Deluge activated by TSE (Thermal Sensing Element)"
    control_category: "Mitigation", cme_kme: "CME"

Return JSON in this exact format:
{
    "intermediate_consequences": [
        "Immediate physical effect 1 (e.g., pressure rises above design)",
        "Immediate physical effect 2 (e.g., leak size and type from table)"
    ],
    "consequences": [
        "Final worst credible outcome 1 (no safeguards assumed)",
        "Final worst credible outcome 2 (e.g., VCE / jet fire if applicable)"
    ],
    "scenario_comments": "Narrative: cause chain → intermediate effects → final impact",
    "consequence_category": "PAF",
    "pec": "PEC-1",
    "current_risk": "C5",
    "drawing_references": [],
    "overpressure_result": {
        "hole_size": "Hole size from Pressure Significance Table (e.g., '6-inches (150 mm)'), or null",
        "significance": "Significance text from table (e.g., 'Stresses greater than yield strength'), or null",
        "consequence_description": "Consequence text from table (e.g., 'Potential for permanent deformation and vessel rupture'), or null",
        "is_vessel_rupture": false,
        "source": "Document name and page reference, or null"
    },
    "mitigation_details": [
        {
            "name": "Full descriptive name of the mitigation",
            "control_category": "Prevention or Detection or Mitigation",
            "cme_kme": "CME or KME"
        }
    ],
    "responsibility": "Role responsible (e.g., Operations Engineer)",
    "planned_residual_risk": {
        "paf": {"consequence": 2, "probability": 1},
        "pd_lor": {"consequence": 2, "probability": 1},
        "ecr": {"consequence": 1, "probability": 1}
    },
    "worst_credible_scenario": "Single sentence describing worst credible outcome"
}

Rules:
- intermediate_consequences: 2-4 immediate physical effects (before final escalation)
- consequences: 2-4 worst credible final impacts (NO safeguards assumed)
- scenario_comments: narrative chain from approved causes → intermediate → final impact
- consequence_category: must be ONE of "PAF", "PD/LOR", "ECR"
- pec: PEC number from the Production-Deck PAF Consequence table lookup
  (e.g., "PEC-1", "PEC-2", "PEC-3"). Use pressure (x-axis) × hole size (y-axis).
- current_risk: if pec = "PEC-1" → "C5"; for all other PEC values read from the table.
  Format: letter + number, e.g. "C5", "D4". Never leave null if pec is populated.
- overpressure_result: populate ONLY for High Pressure deviations where a ratio was provided.
  Set all fields to null for other deviation types.
  hole_size, significance, consequence_description MUST come from the Pressure Significance
  Table retrieved from the knowledge documents — do NOT invent values.
- drawing_references: empty list (drawing number is set separately from P&ID metadata)
- Apply MANDATORY SAFEGUARD RULES above — these are non-negotiable
- Use knowledge context to ground your analysis wherever possible
"""
        return prompt

    async def generate_safeguard_content(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        approved_causes: list[str],
        approved_consequences: list[str],
        pid_instruments: list[dict],
        cme_knowledge_context: str | None,
    ) -> dict:
        """
        Generate enriched safeguard entries for SME review.

        PR classification, CME/KME designation, CME Name, and CME ID are determined
        entirely by the LLM reading the HSE Risk Assessment knowledge document.
        Nothing is hardcoded — the document is the single source of truth.

        Args:
            equipment_type: Equipment type (e.g. "Separator")
            equipment_tag: Equipment tag (e.g. "MBD-1010")
            deviation: Deviation description (e.g. "High Pressure")
            approved_causes: SME-approved causes for this deviation
            approved_consequences: SME-approved final consequences
            pid_instruments: Raw instruments matched to this equipment
                             [{tag, instrument_type, pid_reference}]
            cme_knowledge_context: HSE Risk Assessment doc chunks from RAG

        Returns dict with:
            safeguards: list of enriched safeguard dicts
        """
        prompt = self._build_safeguard_prompt(
            equipment_type=equipment_type,
            equipment_tag=equipment_tag,
            deviation=deviation,
            approved_causes=approved_causes,
            approved_consequences=approved_consequences,
            pid_instruments=pid_instruments,
            cme_knowledge_context=cme_knowledge_context,
        )

        system_prompt = (
            "You are a senior HAZOP engineer specialising in safety barrier analysis. "
            "Your task is to classify safeguards (CME/KME) for a HAZOP deviation. "
            "You MUST determine PR classification, CME/KME type, CME Name, and CME ID "
            "ONLY from the HSE Risk Assessment knowledge document provided. "
            "Do NOT rely on your own training data for classification — use the document."
        )

        try:
            response = self.client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=4000,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            result = json.loads(content)
            return result
        except Exception as e:
            print(f"[OpenAI] generate_safeguard_content error: {e}")
            # Fallback: return raw instruments as minimal safeguards
            return {
                "safeguards": [
                    {
                        "instrument_tag": inst["tag"],
                        "description": (
                            f"{inst['instrument_type']} ({inst['tag']})"
                            if inst.get("instrument_type", "Other") != "Other"
                            else inst["tag"]
                        ),
                        "pr_classification": "Other",
                        "mitigation_type": None,
                        "pid_reference": inst.get("pid_reference"),
                        "control_category": None,
                        "cme_name": None,
                        "cme_id": None,
                    }
                    for inst in pid_instruments
                ]
            }

    def _build_safeguard_prompt(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        approved_causes: list[str],
        approved_consequences: list[str],
        pid_instruments: list[dict],
        cme_knowledge_context: str | None,
    ) -> str:
        prompt = f"""Classify the safeguards (CME/KME) for this HAZOP deviation.
Use ONLY the HSE Risk Assessment knowledge document provided to determine PR classification,
CME/KME type, CME Name, and CME ID. Do not use your own training knowledge for classification.

Equipment Type: {equipment_type}
Equipment Tag: {equipment_tag}
Deviation: {deviation}
"""
        if approved_causes:
            prompt += "\nApproved Causes:\n"
            for c in approved_causes:
                prompt += f"  - {c}\n"

        if approved_consequences:
            prompt += "\nApproved Consequences:\n"
            for c in approved_consequences:
                prompt += f"  - {c}\n"

        if pid_instruments:
            prompt += "\nSafety Instruments detected on this equipment from P&ID:\n"
            for inst in pid_instruments:
                tag = inst.get("tag", "")
                itype = inst.get("instrument_type", "")
                pid_ref = inst.get("pid_reference", "")
                prompt += f"  - {tag} ({itype})"
                if pid_ref:
                    prompt += f" [P&ID: {pid_ref}]"
                prompt += "\n"
        else:
            prompt += "\nNo safety instruments detected on this equipment from P&ID.\n"

        if cme_knowledge_context:
            prompt += (
                f"\nHSE Risk Assessment Knowledge Document (use this to classify safeguards):\n"
                f"{cme_knowledge_context}\n"
            )
        else:
            prompt += (
                "\nNo HSE Risk Assessment document was retrieved. "
                "Classify based on standard HAZOP engineering practice.\n"
            )

        prompt += """
MANDATORY SAFEGUARD RULES (add these if not already in the P&ID instruments list):
- If consequences include Loss of Containment, hydrocarbon release, pressurized leak,
  or vessel rupture: ADD a gas detection safeguard:
    instrument_tag: "Gas Detection System"
    description: "Gas detection (2 detectors at 20% LEL or 1 at 45% LEL — triggers
                  closure of BSDV and XV on each subsea flowline, and SSV and SDV
                  on each dry tree well)"
  (classify using knowledge document)

- If consequences include Jet Fire: ADD a deluge safeguard:
    instrument_tag: "TSE / Deluge"
    description: "Deluge activated by TSE (Thermal Sensing Element)"
  (classify using knowledge document)

Return JSON in this exact format:
{
    "safeguards": [
        {
            "instrument_tag": "PSHH-1010",
            "description": "Full description of what this safeguard does and how it protects",
            "pr_classification": "PR-1",
            "mitigation_type": "CME",
            "pid_reference": "4020",
            "control_category": "Prevention",
            "cme_name": "Safety Instrumented System / ESD (from HSE doc)",
            "cme_id": "CME-001"
        }
    ]
}

Rules:
- description: write what the instrument DOES as a safeguard (e.g., "PSHH-1010 closes BSDV on
  each affected flowline and SSV/SDV on each dry tree well on high pressure signal")
- pr_classification: MUST come from the HSE Risk Assessment document — read the table
- mitigation_type: "CME" or "KME" — from HSE doc
- pid_reference: use the value from the P&ID instrument data if provided, else null
- control_category: "Prevention", "Detection", or "Mitigation" — from HSE doc
- cme_name: full CME name as written in the HSE Risk Assessment document
- cme_id: unique CME identifier from the CME register table in the document (e.g. "CME-001")
- Include ALL P&ID instruments listed above plus any MANDATORY safeguards required by consequences
- Do NOT include non-safety instruments (transmitters, indicators, control valves) as safeguards
"""
        return prompt

    async def generate_causes_and_consequences(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        design_pressure: float | None = None,
        design_temperature: float | None = None,
        existing_safeguards: list[str] | None = None,
        knowledge_context: str | None = None,
    ) -> dict:
        """
        Backward-compatible wrapper for Quick Draft mode.
        Calls generate_deviation_content and returns only causes/consequences.
        """
        result = await self.generate_deviation_content(
            equipment_type=equipment_type,
            equipment_tag=equipment_tag,
            deviation=deviation,
            design_pressure=design_pressure,
            design_temperature=design_temperature,
            existing_safeguards=existing_safeguards,
            knowledge_context=knowledge_context,
        )
        return {
            "causes": result.get("causes", []),
            "consequences": result.get("consequences", []),
            "worst_credible_scenario": result.get("worst_credible_scenario", ""),
            "personnel_exposure": result.get("personnel_exposure", ""),
        }

    async def generate_recommendations(
        self,
        deviation: str,
        consequences: list[str],
        risk_level: str,
        existing_safeguards: list[str],
        knowledge_context: str | None = None,
    ) -> list[str]:
        """
        Generate recommendations when risk level requires action (C, D, E).

        Returns list of actionable recommendation strings.
        """
        prompt = f"""You are a process safety engineer reviewing HAZOP results.

Given this HAZOP finding, generate practical, actionable recommendations.

Deviation: {deviation}
Consequences: {json.dumps(consequences)}
Current Risk Level: {risk_level}
Existing Safeguards: {json.dumps(existing_safeguards)}

{f"Reference Context: {knowledge_context}" if knowledge_context else ""}

Return JSON with format:
{{
    "recommendations": [
        "Specific actionable recommendation 1",
        "Specific actionable recommendation 2"
    ]
}}

Rules:
- Each recommendation must be specific and actionable
- Reference specific equipment or systems where possible
- Do not recommend safeguards that already exist
- Focus on closing the identified gap
- Maximum 5 recommendations
- No generic statements like "improve safety"
"""

        response = self.client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "user", "content": prompt},
                ],
                max_completion_tokens=2000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        result = json.loads(content)
        return result.get("recommendations", [])

    async def estimate_consequence_severity(
        self,
        equipment_type: str,
        deviation: str,
        consequences: list[str],
        design_pressure: float | None = None,
        personnel_count_nearby: int | None = None,
        knowledge_context: str | None = None,
    ) -> dict:
        """
        LLM-assisted severity estimation for PAF, PD/LOR, ECR.

        IMPORTANT: This is a SUGGESTION only. The Risk Engine does the final
        matrix lookup. SME must validate these severity values.

        Returns:
            Dict with suggested severity values and reasoning
        """
        prompt = f"""You are a process safety engineer estimating consequence severity for HAZOP.

Equipment: {equipment_type}
Deviation: {deviation}
Consequences: {json.dumps(consequences)}
{"Design Pressure: " + str(design_pressure) + " PSIG" if design_pressure else ""}
{"Personnel nearby: " + str(personnel_count_nearby) if personnel_count_nearby else ""}

{f"Reference Context: {knowledge_context}" if knowledge_context else ""}

Estimate severity levels (1-5) for each category.

Return JSON:
{{
    "paf_severity": {{
        "value": <1-5>,
        "reasoning": "Brief explanation"
    }},
    "pd_lor_severity": {{
        "value": <1-5>,
        "reasoning": "Brief explanation"
    }},
    "ecr_severity": {{
        "value": <1-5>,
        "reasoning": "Brief explanation"
    }},
    "base_probability": {{
        "value": <1-5>,
        "reasoning": "Brief explanation of likelihood without safeguards"
    }}
}}

Severity scale:
  1 = Negligible
  2 = Minor
  3 = Moderate
  4 = Major
  5 = Catastrophic

Be conservative — when uncertain, estimate higher severity.
This is safety-critical. Over-estimation is safer than under-estimation.
"""

        response = self.client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=2000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        return json.loads(content)

    # ------------------------------------------------------------------
    # Prompt Builders
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        return """You are a senior process safety engineer with expertise in HAZOP studies
for oil and gas facilities.

Your role is to generate realistic, technically accurate causes and consequences
for HAZOP deviations.

Rules:
1. All outputs must be in structured JSON format
2. Causes must be specific and technically plausible
3. Consequences must assume NO safeguards are present (worst credible case)
4. Causes MUST reference a specific instrument tag from the provided P&ID instrument list.
   Do NOT generate generic causes such as "downstream blockage", "fire case",
   "external heat input", "operator error", or any cause that does not cite a specific tag.
   If no instrument in the provided list can plausibly cause this deviation, return causes: []
5. Do NOT invent instrument tags. Only use tags that appear verbatim in the provided list.
   Equipment data is provided for context (design pressures, conditions) only.
6. Do not assign risk scores — that is handled by the deterministic Risk Engine
7. Do not classify safeguards into PR categories — that is handled by the Safeguard Classifier
8. Focus only on cause/consequence reasoning
9. Be conservative — if uncertain, list more severe consequences"""

    def _build_deviation_content_prompt(
        self,
        equipment_type: str,
        equipment_tag: str,
        deviation: str,
        design_pressure: float | None,
        design_temperature: float | None,
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
    ) -> str:
        prompt = f"""Generate complete HAZOP deviation content for this deviation.

Equipment Type: {equipment_type}
Equipment Tag: {equipment_tag}
Deviation: {deviation}
"""
        if pid_summary:
            prompt += f"\nP&ID Overview: {pid_summary}\n"
        if flow_description:
            prompt += f"Process Flow: {flow_description}\n"

        if design_pressure:
            prompt += f"Design Pressure: {design_pressure} PSIG\n"
        if operating_pressure:
            prompt += f"Normal Operating Pressure: {operating_pressure} PSIG\n"
        if upstream_pressure_psig:
            prompt += (
                f"Maximum Upstream Pressure Source: {upstream_pressure_psig} PSIG"
                " (maximum pressure this node could receive from upstream — use for overpressure scenario analysis)\n"
            )
        if design_temperature:
            prompt += f"Design Temperature: {design_temperature} °F\n"

        # Include instruments for this equipment — LLM must derive causes from these tags only
        if node_instruments:
            valid_tag_list = ", ".join(inst.get("tag", "") for inst in node_instruments)
            prompt += f"\nP&ID Instruments for this equipment:\n"
            for inst in node_instruments:
                tag = inst.get("tag", "")
                itype = inst.get("instrument_type", "")
                setpoint = inst.get("setpoint")
                assoc = inst.get("associated_equipment_tag", "")
                line = f"  - {tag} ({itype})"
                if inst.get("position"):
                    line += f", {inst['position']} of equipment"
                if inst.get("line_phase"):
                    line += f", {inst['line_phase']} line"
                if setpoint is not None:
                    line += f", setpoint: {setpoint}"
                if assoc:
                    line += f", associated with: {assoc}"
                prompt += line + "\n"
            prompt += f"\nValid tags for causes: {valid_tag_list}\n"
            prompt += "ONLY reference tags from this list. Any other tag is invalid.\n"
        else:
            prompt += (
                "\nNo control valves or process controllers are associated with this "
                "equipment in the P&ID data. Return causes: [] — do NOT generate generic "
                "causes to fill the list.\n"
            )

        # Include other equipment in the node for context (design pressures, conditions)
        if node_equipment:
            prompt += "\nOther Equipment in this node (for context only — not root causes):\n"
            for eq in node_equipment:
                etag = eq.get("tag", "")
                etype = eq.get("equipment_type", "")
                dp = eq.get("design_pressure")
                if etag != equipment_tag:  # Skip the current equipment
                    line = f"  - {etag} ({etype})"
                    if dp is not None:
                        line += f", design pressure: {dp} PSIG"
                    prompt += line + "\n"

        if existing_safeguards:
            prompt += f"\nKnown Safeguards (for context only, NOT for consequence evaluation): {json.dumps(existing_safeguards)}\n"

        # Inject P&ID line connectivity
        if line_connectivity:
            prompt += "\nProcess Connections (from P&ID):\n"
            for lc in line_connectivity:
                from_t = lc.get("from_tag", "")
                to_t = lc.get("to_tag", "")
                desc = lc.get("description") or f"{from_t} → {to_t}"
                phase = lc.get("fluid_phase") or ""
                size = lc.get("pipe_size") or ""
                detail = ", ".join(x for x in [phase, size] if x)
                prompt += f"  - {desc}" + (f" ({detail})" if detail else "") + "\n"

        # Inject control loop information
        if control_loops:
            prompt += "\nControl Loops (from P&ID):\n"
            for cl in control_loops:
                var = cl.get("controlled_variable", "")
                me = cl.get("measuring_element") or "?"
                ctrl = cl.get("controller") or "?"
                fe = cl.get("final_element", "?")
                eq = cl.get("controlled_equipment", "?")
                prompt += f"  - {var} loop: {me} → {ctrl} → {fe} on {eq}\n"
                if cl.get("description"):
                    prompt += f"    ({cl['description']})\n"

        # Inject deviation location context for the current equipment
        if deviation_locations:
            for dl in deviation_locations:
                if dl.get("equipment_tag") == equipment_tag:
                    devs = dl.get("susceptible_deviations") or []
                    loc_desc = dl.get("location_description") or ""
                    if devs:
                        prompt += f"\nDeviation Location Context for {equipment_tag}:\n"
                        prompt += f"  Susceptible to: {', '.join(devs)}\n"
                        if loc_desc:
                            prompt += f"  Location: {loc_desc}\n"
                    break

        # Special instructions for Human Factors and Previous Incidents
        if is_special_category and "Human Factors" in deviation:
            prompt += """
SPECIAL INSTRUCTION: This is the "Human Factors" deviation category.
Focus on human-related causes such as:
- Operator errors (wrong valve, missed alarm, incorrect procedure)
- Maintenance errors (improper isolation, incorrect reassembly)
- Training gaps
- Fatigue, workload, shift handover issues
- Procedural non-compliance
- Communication failures
Consequences should address what happens when human errors occur in the context of this equipment.
"""
        elif is_special_category and "Previous Incidents" in deviation:
            prompt += """
SPECIAL INSTRUCTION: This is the "Previous Incidents / Learnings" deviation category.
Focus on:
- Common industry incidents for this equipment type
- Known failure modes from historical events (e.g., Texas City, Piper Alpha, Buncefield)
- Recurring themes in incident databases (API, CCPS, HSE)
- Lessons learned that apply to this equipment type
Causes should be based on known historical failure patterns.
Consequences should reflect actual incident outcomes from industry experience.
"""

        prompt += """
Return JSON in this exact format:
{
    "causes": [
        {"description": "Full cause description referencing the instrument", "tag": "INST-TAG"},
        {"description": "Another cause referencing a different instrument", "tag": "INST-TAG2"}
    ],
    "drawing_references": ["DWG reference if found in knowledge context"],
    "intermediate_consequences": [
        "Immediate effect 1 (e.g., pressure exceeds design)",
        "Immediate effect 2"
    ],
    "consequences": [
        "Final impact / worst credible outcome 1 (no safeguards assumed)",
        "Final impact 2"
    ],
    "scenario_comments": "Narrative describing the worst credible scenario from cause to final impact",
    "consequence_category": "PAF or PD/LOR or ECR",
    "personnel_exposure": "<5 or 5-14 or >14",
    "mitigation_details": [
        {
            "name": "Full descriptive name of the mitigation (e.g., PSHH-1210 Emergency Shutdown)",
            "control_category": "Prevention or Detection or Mitigation",
            "cme_kme": "CME or KME"
        }
    ],
    "recommendations": [
        "Specific actionable recommendation 1",
        "Specific actionable recommendation 2"
    ],
    "responsibility": "Role responsible for recommendations (e.g., Operations Engineer)",
    "planned_residual_risk": {
        "paf": {"consequence": 2, "probability": 1},
        "pd_lor": {"consequence": 2, "probability": 1},
        "ecr": {"consequence": 1, "probability": 1}
    },
    "worst_credible_scenario": "Single sentence describing the worst credible outcome"
}

Rules:
- Each cause must be an object with "description" (string) and "tag" (the instrument tag from the valid list).
- "tag" must exactly match one of the tags in the "Valid tags for causes" list.
- If no instrument in the valid list can cause this deviation, return causes: []
- Do NOT generate generic causes (fire case, operator error, downstream blockage, etc.)
- Only include causes that are directly traceable to an instrument failure in the valid list.
- intermediate_consequences: 2-4 immediate effects (before escalation)
- consequences: 2-4 final impacts (worst credible, no safeguards assumed)
- scenario_comments: narrative chain from cause → intermediate → final impact
- drawing_references: extract from P&ID context if available, otherwise empty list
- mitigation_details: suggest relevant mitigations based on the P&ID equipment and instruments, otherwise empty list
- planned_residual_risk: estimate post-recommendation risk severity (1-5) and probability (1-5)
- Be specific to the equipment type, deviation, and the actual P&ID layout provided
- Ground your answers in the P&ID information (equipment, instruments, line connectivity, control loops)
"""
        return prompt


# Module-level singleton
openai_service = OpenAIService()
