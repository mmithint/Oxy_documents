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

IMPORTANT: You are NOT limited to a fixed list of types. Extract every device you find
and provide its full descriptive type name based on ISA/industry standards.

Common equipment examples (NOT exhaustive — use whatever fits):
  Separator, Header, Compressor, Pump, Heat Exchanger, Vessel, Tank, Scrubber,
  Knockout Drum, Flare, Column, Reactor, Filter, Mixer, etc.

Common instrument examples (NOT exhaustive — use whatever fits):
  Pressure Switch High High, Pressure Safety Valve, Pressure Control Valve,
  Pressure Transmitter, Pressure Indicator, Pressure Differential Indicator,
  Level Switch High High, Level Switch Low Low, Level Control Valve,
  Level Transmitter, Level Gauge, Level Indicator,
  Temperature Switch High High, Temperature Transmitter, Temperature Indicator,
  Flow Safety Valve, Flow Control Valve, Flow Transmitter,
  Gas Detector, Fire Detector, Deluge System, Emergency Shutdown Valve,
  Blowdown Valve, Control Valve, Solenoid Valve, Check Valve, etc.

If you see a tag you don't recognize, still include it with the best descriptive
type name you can determine from context or ISA designation. Use "Other" only as
a last resort.

Return JSON in this exact format:
{
    "node_name": "Descriptive name of the P&ID node/system",
    "system": "Parent system name (e.g. Hydrocarbon Processing Systems)",
    "description": "Brief description of what this P&ID covers",
    "equipment": [
        {
            "tag": "V-1210",
            "name": "HP Oil Production Separator No. 2",
            "equipment_type": "Separator",
            "design_pressure": 450.0,
            "design_temperature": 200.0,
            "operating_pressure": 350.0,
            "operating_temperature": 150.0
        }
    ],
    "instruments": [
        {
            "tag": "PSHH-1210",
            "instrument_type": "Pressure Switch High High",
            "setpoint": 440.0,
            "associated_equipment_tag": "V-1210"
        }
    ]
}

Rules:
- Extract EVERY equipment tag and instrument tag you can find — do not skip any
- Use null for numeric values you cannot determine from the text
- Associate instruments with equipment using shared numeric suffixes (e.g., PSHH-1210 → V-1210)
- Do NOT invent tags that aren't in the text
- Pressure values are typically in PSIG, temperature in °F"""

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
                temperature=0.1,
                max_tokens=4000,
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

IMPORTANT:
  - Read EVERY tag you see, even if partially visible or small
  - Tags inside circles are instruments — read the letters and numbers carefully
  - Tags inside or near boxes/vessels are equipment
  - You are NOT limited to a fixed list of types — use ISA standard naming
  - Pay special attention to: Level Gauges (LG), Level Transmitters (LT),
    Pressure Indicators (PI), Temperature Indicators (TI), and other commonly
    missed instruments that appear as small circles on the diagram

Return JSON in this exact format:
{
    "equipment": [
        {
            "tag": "V-1210",
            "name": "HP Oil Production Separator",
            "equipment_type": "Separator",
            "design_pressure": null,
            "design_temperature": null,
            "operating_pressure": null,
            "operating_temperature": null
        }
    ],
    "instruments": [
        {
            "tag": "PSHH-1210",
            "instrument_type": "Pressure Switch High High",
            "setpoint": null,
            "associated_equipment_tag": "V-1210"
        }
    ]
}

Rules:
- Extract EVERY tag visible in the image — do not skip any
- Associate instruments with their connected equipment using visual connections or shared numeric suffixes
- Use null for values you cannot read from the image
- Do NOT invent tags — only report what you actually see
- If text is partially readable, include your best interpretation"""

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
                temperature=0.1,
                max_tokens=4000,
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
        knowledge_context: str | None = None,
        is_special_category: bool = False,
        node_instruments: list[dict] | None = None,
        node_equipment: list[dict] | None = None,
    ) -> dict:
        """
        Generate full HAZOP deviation content: causes, consequences,
        intermediate consequences, scenario, mitigations, PEC,
        recommendations, responsibility, planned residual risk.

        Uses a single expanded LLM call with structured JSON output.
        Knowledge context from RAG is injected to ground the response.

        Args:
            equipment_type: Type of equipment (e.g., "Separator")
            equipment_tag: Equipment tag (e.g., "V-1210")
            deviation: Deviation name (e.g., "High Pressure")
            design_pressure: Design pressure if known
            design_temperature: Design temperature if known
            existing_safeguards: List of safeguard descriptions already detected
            knowledge_context: Retrieved knowledge chunks for grounding
            is_special_category: True for Human Factors / Previous Incidents

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
            knowledge_context=knowledge_context,
            is_special_category=is_special_category,
            node_instruments=node_instruments,
            node_equipment=node_equipment,
        )

        response = self.client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
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
            temperature=0.2,
            max_tokens=1000,
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
            temperature=0.1,  # Very low temperature for consistent severity estimates
            max_tokens=1000,
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
        knowledge_context: str | None = None,
        is_special_category: bool = False,
        node_instruments: list[dict] | None = None,
        node_equipment: list[dict] | None = None,
    ) -> str:
        prompt = f"""Generate complete HAZOP deviation content for this deviation.

Equipment Type: {equipment_type}
Equipment Tag: {equipment_tag}
Deviation: {deviation}
"""

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

        if knowledge_context:
            prompt += f"\nRelevant Knowledge Context (from company documents):\n{knowledge_context}\n"

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
- drawing_references: extract from knowledge context if available, otherwise empty list
- mitigation_details: extract from knowledge context if available, otherwise empty list
- planned_residual_risk: estimate post-recommendation risk severity (1-5) and probability (1-5)
- Be specific to the equipment type and deviation
- Use knowledge context to ground your answers where possible
"""
        return prompt


# Module-level singleton
openai_service = OpenAIService()
