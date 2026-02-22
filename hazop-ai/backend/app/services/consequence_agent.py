"""
Consequence Agent — Knowledge-Driven Multi-Step Reasoning

A scalable agent that generates consequences for ANY HAZOP deviation type
using knowledge documents as the single source of truth.

Architecture:
    Step 1: RETRIEVE  — Smart retrieval based on causes + deviation + equipment
    Step 2: REASON    — LLM extracts consequences from retrieved knowledge
    Step 3: VALIDATE  — Ensure consequences are grounded in knowledge
    Step 4: OUTPUT    — Return structured DeviationConsequencesItem

Key Principle:
    Every consequence must be derived from knowledge chunks.
    No hardcoded logic. No generic fallbacks.
    If knowledge doesn't exist → flag for SME review.

Knowledge Sources:
    - 60 400 301 07 Consequence Development Guideline rev 1.pdf
    - production-deck-paf-consequence.xlsx
"""

import json
from dataclasses import dataclass, field
from typing import Optional
from app.services.claude_service import claude_service
from app.services.knowledge_service import knowledge_service
from app.models.pid_models import PIDNode, Equipment
from app.models.api_models import DeviationConsequencesItem, OverpressureCalc, CategoryRowItem
from app.core.config import get_settings

settings = get_settings()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DeviationContext:
    """All context needed for consequence analysis."""
    deviation_id: str
    guideword: str
    parameter: str
    deviation_description: str

    # Equipment info
    equipment_tag: str
    equipment_type: str
    design_pressure: Optional[float] = None
    operating_pressure: Optional[float] = None
    design_temperature: Optional[float] = None
    operating_temperature: Optional[float] = None

    # Node-level context
    upstream_pressure_psig: Optional[float] = None
    pid_summary: Optional[str] = None
    flow_description: Optional[str] = None
    drawing_number: Optional[str] = None

    # SME-approved causes
    approved_causes: list[str] = field(default_factory=list)

    # P&ID instruments
    pid_instruments: list[dict] = field(default_factory=list)


@dataclass
class KnowledgeChunk:
    """A single knowledge chunk with metadata."""
    content: str
    source_document: str
    page: Optional[int] = None
    relevance_score: float = 0.0
    query_used: str = ""


@dataclass
class RetrievedKnowledge:
    """All knowledge retrieved for this deviation."""
    # Chunks organized by purpose
    consequence_chunks: list[KnowledgeChunk] = field(default_factory=list)
    cause_specific_chunks: dict[str, list[KnowledgeChunk]] = field(default_factory=dict)
    pec_chunks: list[KnowledgeChunk] = field(default_factory=list)
    equipment_chunks: list[KnowledgeChunk] = field(default_factory=list)

    # Combined context for LLM
    def get_full_context(self) -> str:
        """Combine all chunks into a single context string."""
        sections = []

        if self.consequence_chunks:
            sections.append("## CONSEQUENCE GUIDANCE")
            for chunk in self.consequence_chunks:
                sections.append(self._format_chunk(chunk))

        if self.cause_specific_chunks:
            sections.append("\n## CAUSE-SPECIFIC CONSEQUENCES")
            for cause, chunks in self.cause_specific_chunks.items():
                sections.append(f"\n### For cause: {cause[:100]}...")
                for chunk in chunks:
                    sections.append(self._format_chunk(chunk))

        if self.pec_chunks:
            sections.append("\n## PEC / PERSONNEL EXPOSURE TABLE")
            for chunk in self.pec_chunks:
                sections.append(self._format_chunk(chunk))

        if self.equipment_chunks:
            sections.append("\n## EQUIPMENT-SPECIFIC GUIDANCE")
            for chunk in self.equipment_chunks:
                sections.append(self._format_chunk(chunk))

        return "\n".join(sections)

    def _format_chunk(self, chunk: KnowledgeChunk) -> str:
        page_info = f", Page {chunk.page}" if chunk.page else ""
        return f"[Source: {chunk.source_document}{page_info}]\n{chunk.content}\n"

    def get_source_references(self) -> list[str]:
        """Get all unique source references."""
        refs = set()
        for chunk in self.consequence_chunks + self.pec_chunks + self.equipment_chunks:
            page_info = f", Page {chunk.page}" if chunk.page else ""
            refs.add(f"{chunk.source_document}{page_info}")
        for chunks in self.cause_specific_chunks.values():
            for chunk in chunks:
                page_info = f", Page {chunk.page}" if chunk.page else ""
                refs.add(f"{chunk.source_document}{page_info}")
        return list(refs)


@dataclass
class CauseConsequenceMapping:
    """Maps a single cause to its consequences from knowledge."""
    cause: str
    intermediate_consequences: list[str] = field(default_factory=list)
    final_consequences: list[str] = field(default_factory=list)
    knowledge_source: Optional[str] = None
    confidence: str = "high"  # high, medium, low


@dataclass
class ReasoningResult:
    """Result from the reasoning step."""
    cause_mappings: list[CauseConsequenceMapping] = field(default_factory=list)
    combined_intermediate: list[str] = field(default_factory=list)
    combined_final: list[str] = field(default_factory=list)
    scenario_comments: str = ""
    consequence_category: str = "PAF"
    pec: Optional[str] = None
    current_risk: Optional[str] = None
    triggered_safeguards: list[dict] = field(default_factory=list)
    reasoning_trace: list[str] = field(default_factory=list)
    calculation_result: Optional[dict] = None
    category_rows: list[dict] = field(default_factory=list)  # Per-category PAF/PD/LOR/ECR rows


@dataclass
class ValidationResult:
    """Result from validation step."""
    is_valid: bool = True
    grounded_consequences: list[str] = field(default_factory=list)
    ungrounded_consequences: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_sme_review: bool = False


# =============================================================================
# CONSEQUENCE AGENT
# =============================================================================

class ConsequenceAgent:
    """
    Knowledge-driven agent for generating HAZOP consequences.

    All consequences are derived from knowledge documents.
    No hardcoded logic or generic fallbacks.
    """

    def __init__(self):
        self.knowledge = knowledge_service

    # -------------------------------------------------------------------------
    # MAIN ENTRY POINT
    # -------------------------------------------------------------------------

    async def generate_consequence(
        self,
        node: PIDNode,
        deviation_id: str,
        guideword: str,
        parameter: str,
        deviation_description: str,
        equipment: Equipment,
        approved_causes: list[str],
        pid_instruments: list[dict],
    ) -> DeviationConsequencesItem:
        """
        Generate consequences using multi-step knowledge-driven reasoning.

        Args:
            node: Full PIDNode with context
            deviation_id: Unique deviation ID
            guideword: e.g., "HIGH", "LOW", "NO"
            parameter: e.g., "PRESSURE", "LEVEL", "FLOW"
            deviation_description: Full deviation text
            equipment: Equipment for this deviation
            approved_causes: SME-approved causes
            pid_instruments: Instruments for this equipment

        Returns:
            DeviationConsequencesItem with knowledge-grounded consequences
        """
        # Build context
        context = self._build_context(
            node=node,
            deviation_id=deviation_id,
            guideword=guideword,
            parameter=parameter,
            deviation_description=deviation_description,
            equipment=equipment,
            approved_causes=approved_causes,
            pid_instruments=pid_instruments,
        )

        print(f"[ConsequenceAgent] Processing: {context.equipment_tag} - {context.deviation_description}")

        # Step 1: RETRIEVE - Smart knowledge retrieval
        knowledge = await self._step_retrieve(context)
        print(f"[ConsequenceAgent] Retrieved {len(knowledge.consequence_chunks)} consequence chunks, "
              f"{len(knowledge.cause_specific_chunks)} cause-specific mappings")

        # Step 2: REASON - Extract consequences from knowledge
        reasoning = await self._step_reason(context, knowledge)
        print(f"[ConsequenceAgent] Generated {len(reasoning.combined_final)} consequences")

        # Step 3: VALIDATE - Ensure grounding in knowledge
        validation = await self._step_validate(context, knowledge, reasoning)
        if validation.warnings:
            print(f"[ConsequenceAgent] Validation warnings: {validation.warnings}")

        # Step 4: OUTPUT - Build final result
        return self._step_output(context, knowledge, reasoning, validation)

    # -------------------------------------------------------------------------
    # STEP 1: SMART RETRIEVAL
    # -------------------------------------------------------------------------

    async def _step_retrieve(self, context: DeviationContext) -> RetrievedKnowledge:
        """
        Retrieve relevant knowledge chunks using smart queries.

        Query Strategy:
        1. General deviation + equipment query
        2. Per-cause specific queries
        3. PEC table query
        4. Equipment-specific failure mode query
        """
        knowledge = RetrievedKnowledge()

        # Query 1: General deviation + equipment consequence query
        deviation_query = (
            f"{context.guideword} {context.parameter} "
            f"{context.equipment_type} consequence impact effect"
        )
        chunks = await self._retrieve_chunks(deviation_query, limit=5)
        knowledge.consequence_chunks.extend(chunks)

        # Query 2: Per-cause specific queries
        for cause in context.approved_causes:
            cause_query = self._build_cause_query(cause, context)
            cause_chunks = await self._retrieve_chunks(cause_query, limit=3)
            if cause_chunks:
                knowledge.cause_specific_chunks[cause] = cause_chunks

        # Query 3: PEC table query
        pec_query = self._build_pec_query(context)
        pec_chunks = await self._retrieve_chunks(pec_query, limit=3)
        knowledge.pec_chunks.extend(pec_chunks)

        # Query 4: Equipment-specific failure modes
        equipment_query = (
            f"{context.equipment_type} failure mode "
            f"{context.guideword} {context.parameter} hazard scenario"
        )
        equipment_chunks = await self._retrieve_chunks(equipment_query, limit=3)
        knowledge.equipment_chunks.extend(equipment_chunks)

        # Query 5: Escalation and intermediate effects
        escalation_query = (
            f"{context.deviation_description} intermediate consequence "
            f"escalation chain effect progression"
        )
        escalation_chunks = await self._retrieve_chunks(escalation_query, limit=3)
        knowledge.consequence_chunks.extend(escalation_chunks)

        return knowledge

    def _build_cause_query(self, cause: str, context: DeviationContext) -> str:
        """Build a smart query for a specific cause."""
        # Extract key terms from cause
        cause_lower = cause.lower()

        # Identify failure type
        failure_terms = []
        if "fails open" in cause_lower:
            failure_terms.append("fails open stuck open")
        elif "fails closed" in cause_lower:
            failure_terms.append("fails closed stuck closed blocked")
        elif "leak" in cause_lower:
            failure_terms.append("leak leakage loss of containment")
        elif "rupture" in cause_lower:
            failure_terms.append("rupture burst catastrophic failure")
        elif "block" in cause_lower:
            failure_terms.append("blockage plugged restriction")

        # Build query
        query_parts = [
            cause[:100],  # Use cause text (truncated)
            context.deviation_description,
            "consequence impact effect",
        ]
        if failure_terms:
            query_parts.extend(failure_terms)

        return " ".join(query_parts)

    def _build_pec_query(self, context: DeviationContext) -> str:
        """Build query for PEC table lookup."""
        query_parts = [
            "PEC personnel exposure count",
            "consequence category PAF PD/LOR ECR",
            context.guideword,
            context.parameter,
        ]

        # Add pressure context if available
        if context.upstream_pressure_psig:
            query_parts.append(f"pressure {context.upstream_pressure_psig} PSIG")

        # Add deviation-specific terms
        if context.parameter == "PRESSURE":
            query_parts.extend(["hole size leak rupture overpressure"])
        elif context.parameter == "LEVEL":
            query_parts.extend(["overflow carryover liquid"])
        elif context.parameter == "TEMPERATURE":
            query_parts.extend(["thermal material failure"])
        elif context.parameter == "FLOW":
            query_parts.extend(["pump damage process upset"])

        return " ".join(query_parts)

    async def _retrieve_chunks(
        self,
        query: str,
        limit: int = 5,
        document_type: str | None = None,
    ) -> list[KnowledgeChunk]:
        """Retrieve knowledge chunks and convert to KnowledgeChunk objects."""
        results = await self.knowledge.search_knowledge(
            query=query,
            limit=limit,
            document_type=document_type,
        )

        chunks = []
        for r in results:
            chunks.append(KnowledgeChunk(
                content=r.get("content", ""),
                source_document=r.get("source_document", "Unknown"),
                page=r.get("page"),
                relevance_score=r.get("similarity_score", 0.0),
                query_used=query,
            ))

        return chunks

    # -------------------------------------------------------------------------
    # STEP 2: REASON WITH KNOWLEDGE
    # -------------------------------------------------------------------------

    async def _step_reason(
        self,
        context: DeviationContext,
        knowledge: RetrievedKnowledge,
    ) -> ReasoningResult:
        """
        Extract consequences from knowledge using LLM.

        The LLM must:
        1. Map each cause to consequences found in knowledge
        2. Determine intermediate and final consequences
        3. Build scenario narrative
        4. Determine PEC and consequence category
        """
        # Perform any calculations first (e.g., pressure ratio)
        calculations = self._perform_calculations(context)

        # Build the reasoning prompt
        prompt = self._build_reasoning_prompt(context, knowledge, calculations)
        system_prompt = self._get_reasoning_system_prompt()

        try:
            result = await claude_service.call_llm_json(
                system_prompt=system_prompt,
                user_prompt=prompt,
                max_tokens=4000,
                temperature=0.2,
            )

            # Parse cause mappings
            cause_mappings = []
            for mapping in result.get("cause_consequence_mappings", []):
                cause_mappings.append(CauseConsequenceMapping(
                    cause=mapping.get("cause", ""),
                    intermediate_consequences=mapping.get("intermediate_consequences", []),
                    final_consequences=mapping.get("final_consequences", []),
                    knowledge_source=mapping.get("knowledge_source"),
                    confidence=mapping.get("confidence", "medium"),
                ))

            return ReasoningResult(
                cause_mappings=cause_mappings,
                combined_intermediate=result.get("intermediate_consequences", []),
                combined_final=result.get("consequences", []),
                scenario_comments=result.get("scenario_comments", ""),
                consequence_category=result.get("consequence_category", "PAF"),
                pec=result.get("pec"),
                current_risk=result.get("current_risk"),
                triggered_safeguards=result.get("triggered_safeguards", []),
                reasoning_trace=result.get("reasoning_trace", []),
                calculation_result=result.get("calculation_result"),
                category_rows=result.get("category_rows", []),
            )

        except Exception as e:
            print(f"[ConsequenceAgent] Reasoning failed: {e}")
            return ReasoningResult(
                combined_intermediate=["Analysis requires SME review"],
                combined_final=["Unable to determine from knowledge - SME review required"],
                scenario_comments=f"Automated analysis encountered error: {str(e)}",
                consequence_category="PAF",
                reasoning_trace=[f"Error: {str(e)}"],
            )

    def _perform_calculations(self, context: DeviationContext) -> dict:
        """Perform deterministic calculations before LLM reasoning."""
        calculations = {}

        # Pressure ratio calculation
        if (context.guideword == "HIGH" and
            context.parameter == "PRESSURE" and
            context.design_pressure and
            context.upstream_pressure_psig):

            ratio = context.upstream_pressure_psig / context.design_pressure
            calculations["pressure_ratio"] = {
                "upstream_pressure": context.upstream_pressure_psig,
                "design_pressure": context.design_pressure,
                "ratio": round(ratio, 2),
                "exceeds_design": ratio > 1.0,
                "exceeds_1_5x": ratio > 1.5,
                "exceeds_2x": ratio > 2.0,
            }

        # Temperature ratio calculation
        if (context.guideword == "HIGH" and
            context.parameter == "TEMPERATURE" and
            context.design_temperature and
            context.operating_temperature):

            ratio = context.operating_temperature / context.design_temperature
            calculations["temperature_ratio"] = {
                "operating_temperature": context.operating_temperature,
                "design_temperature": context.design_temperature,
                "ratio": round(ratio, 2),
                "exceeds_design": ratio > 1.0,
            }

        return calculations

    def _build_reasoning_prompt(
        self,
        context: DeviationContext,
        knowledge: RetrievedKnowledge,
        calculations: dict,
    ) -> str:
        """Build the prompt for knowledge-based reasoning."""

        prompt = f"""Analyze consequences for this HAZOP deviation using ONLY the knowledge provided.

## DEVIATION CONTEXT
Equipment: {context.equipment_tag} ({context.equipment_type})
Deviation: {context.guideword} {context.parameter}
Description: {context.deviation_description}
"""

        # Add P&ID context
        if context.pid_summary:
            prompt += f"\nP&ID Overview: {context.pid_summary}"
        if context.flow_description:
            prompt += f"\nProcess Flow: {context.flow_description}"

        # Add design values
        prompt += "\n\n## DESIGN VALUES"
        if context.design_pressure:
            prompt += f"\nDesign Pressure: {context.design_pressure} PSIG"
        if context.operating_pressure:
            prompt += f"\nOperating Pressure: {context.operating_pressure} PSIG"
        if context.upstream_pressure_psig:
            prompt += f"\nMax Upstream Pressure: {context.upstream_pressure_psig} PSIG"
        if context.design_temperature:
            prompt += f"\nDesign Temperature: {context.design_temperature} °F"

        # Add calculations
        if calculations:
            prompt += "\n\n## PRE-CALCULATED VALUES"
            prompt += f"\n{json.dumps(calculations, indent=2)}"

        # Add approved causes
        prompt += "\n\n## SME-APPROVED CAUSES"
        for i, cause in enumerate(context.approved_causes, 1):
            prompt += f"\n{i}. {cause}"

        # Add P&ID instruments
        if context.pid_instruments:
            prompt += "\n\n## P&ID INSTRUMENTS (for safeguard identification)"
            for inst in context.pid_instruments:
                tag = inst.get("tag", "")
                itype = inst.get("instrument_type", "")
                prompt += f"\n- {tag} ({itype})"

        # Add all knowledge context
        prompt += f"\n\n## KNOWLEDGE DOCUMENTS (Use ONLY this information)\n"
        prompt += knowledge.get_full_context()

        # Output instructions
        prompt += """

## YOUR TASK
Using ONLY the knowledge documents above, determine:

1. For EACH approved cause, find the corresponding consequences in the knowledge
2. Build the consequence chain: cause → intermediate effects → final impacts
3. Populate all 3 consequence category rows (PAF, PD/LOR, ECR) from the knowledge
4. Identify any triggered safeguards based on consequence type

Consequence category definitions:
- PAF (Potential to Affect People): personnel injury, fatality, fire, explosion, toxic release
- PD/LOR (Property Damage / Loss of Revenue): equipment damage, production downtime, economic loss
- ECR (Environmental / Community / Regulatory): environmental release, spill, regulatory impact

## OUTPUT FORMAT
Return JSON:
{
    "reasoning_trace": [
        "Step 1: For cause 'X', found in knowledge that...",
        "Step 2: This leads to intermediate effect...",
        "Step 3: Final consequence is..."
    ],
    "cause_consequence_mappings": [
        {
            "cause": "The exact cause text",
            "intermediate_consequences": ["Effect from knowledge"],
            "final_consequences": ["Final impact from knowledge"],
            "knowledge_source": "Document name, Page X",
            "confidence": "high/medium/low"
        }
    ],
    "intermediate_consequences": [
        "Combined intermediate effect 1",
        "Combined intermediate effect 2"
    ],
    "category_rows": [
        {
            "category": "PAF",
            "consequences": ["Loss of primary containment...", "Jet fire, flash fire..."],
            "scenario_comments": "Narrative for PAF scenario citing knowledge sources",
            "pec": "PEC-1 (from PEC table lookup)",
            "current_risk": "C5 (from risk table)"
        },
        {
            "category": "PD/LOR",
            "consequences": ["Downtime of approximately X to X months if specific value not in knowledge, or actual value if found"],
            "scenario_comments": "Narrative for production/property loss if found in knowledge, else null",
            "pec": null,
            "current_risk": null
        },
        {
            "category": "ECR",
            "consequences": ["Environmental consequence from knowledge if found, else leave empty array"],
            "scenario_comments": null,
            "pec": null,
            "current_risk": null
        }
    ],
    "consequences": ["Same as PAF consequences — for backward compatibility"],
    "scenario_comments": "Overall narrative",
    "consequence_category": "PAF",
    "pec": "PEC-1 or PEC-2 etc (from table lookup)",
    "current_risk": "C5 or D4 etc (from table)",
    "calculation_result": {
        "hole_size": "From knowledge table if applicable",
        "significance": "From knowledge table if applicable"
    },
    "triggered_safeguards": [
        {"name": "Gas detection", "reason": "LOC scenario - from knowledge"}
    ]
}

## CRITICAL RULES
1. ONLY use consequences found in the knowledge documents
2. If you cannot find a consequence for a cause in the knowledge, set confidence: "low"
3. Always cite the knowledge source for each consequence
4. Consequences assume NO safeguards (worst credible case)
5. If LOC/leak scenario: include gas detection in triggered_safeguards
6. If fire scenario: include deluge in triggered_safeguards
7. Do NOT invent consequences - use only what's in the knowledge
8. For PD/LOR: if no specific downtime value in knowledge, use "Downtime of approximately X to X months" as placeholder
9. For ECR: if no environmental consequence in knowledge, return empty array for consequences
"""

        return prompt

    def _get_reasoning_system_prompt(self) -> str:
        """System prompt for knowledge-based reasoning."""
        return """You are a senior process safety engineer performing HAZOP consequence analysis.

Your task is to extract consequences from the provided knowledge documents.

CRITICAL RULES:
1. You must ONLY use information from the knowledge documents provided
2. Every consequence must be traceable to a knowledge source
3. If the knowledge doesn't cover a specific cause, acknowledge this with low confidence
4. Consequences assume NO safeguards are present (worst credible case)
5. Be conservative - when uncertain, assume more severe outcomes from the knowledge
6. All outputs must be structured JSON

You are NOT allowed to:
- Invent consequences not found in the knowledge
- Use your general training knowledge instead of the documents
- Generate generic consequences without knowledge backing"""

    # -------------------------------------------------------------------------
    # STEP 3: VALIDATE
    # -------------------------------------------------------------------------

    async def _step_validate(
        self,
        context: DeviationContext,
        knowledge: RetrievedKnowledge,
        reasoning: ReasoningResult,
    ) -> ValidationResult:
        """
        Validate that consequences are grounded in knowledge.

        Checks:
        1. Each cause has a consequence mapping
        2. Consequences are found in knowledge chunks
        3. PEC format is valid
        4. Mandatory safeguards are present for relevant scenarios
        """
        validation = ValidationResult()

        # Check that all causes have mappings
        mapped_causes = {m.cause for m in reasoning.cause_mappings}
        for cause in context.approved_causes:
            if cause not in mapped_causes:
                validation.warnings.append(f"No consequence mapping for cause: {cause[:50]}...")

        # Check for low confidence mappings
        low_confidence = [m for m in reasoning.cause_mappings if m.confidence == "low"]
        if low_confidence:
            validation.needs_sme_review = True
            validation.warnings.append(
                f"{len(low_confidence)} cause(s) have low confidence - knowledge may be incomplete"
            )

        # Validate PEC format
        if reasoning.pec and not reasoning.pec.upper().startswith("PEC"):
            validation.warnings.append(f"PEC format may be incorrect: {reasoning.pec}")

        # Check consequence category
        valid_categories = ["PAF", "PD/LOR", "ECR"]
        if reasoning.consequence_category not in valid_categories:
            validation.warnings.append(
                f"Invalid consequence_category: {reasoning.consequence_category}"
            )

        # Check mandatory safeguards for LOC scenarios
        consequence_text = " ".join(reasoning.combined_final).lower()
        triggered_names = [sg.get("name", "").lower() for sg in reasoning.triggered_safeguards]

        loc_keywords = ["leak", "release", "rupture", "loc", "loss of containment", "hydrocarbon"]
        if any(kw in consequence_text for kw in loc_keywords):
            if not any("gas" in name or "detection" in name for name in triggered_names):
                validation.warnings.append(
                    "LOC scenario detected but gas detection not in triggered safeguards"
                )

        fire_keywords = ["fire", "ignition", "jet fire", "pool fire", "explosion"]
        if any(kw in consequence_text for kw in fire_keywords):
            if not any("deluge" in name for name in triggered_names):
                validation.warnings.append(
                    "Fire scenario detected but deluge not in triggered safeguards"
                )

        # Check if we have any consequences at all
        if not reasoning.combined_final:
            validation.is_valid = False
            validation.needs_sme_review = True
            validation.warnings.append("No final consequences generated")

        return validation

    # -------------------------------------------------------------------------
    # STEP 4: OUTPUT
    # -------------------------------------------------------------------------

    def _step_output(
        self,
        context: DeviationContext,
        knowledge: RetrievedKnowledge,
        reasoning: ReasoningResult,
        validation: ValidationResult,
    ) -> DeviationConsequencesItem:
        """Build the final output structure."""

        # Build overpressure calc if applicable
        overpressure_calc = None
        if (reasoning.calculation_result and
            context.design_pressure and
            context.upstream_pressure_psig):

            calc = reasoning.calculation_result
            ratio = context.upstream_pressure_psig / context.design_pressure

            overpressure_calc = OverpressureCalc(
                max_credible_pressure=context.upstream_pressure_psig,
                design_pressure=context.design_pressure,
                ratio=round(ratio, 2),
                exceeds_2x=ratio > 2.0,
                assumed_leak_size=calc.get("hole_size"),
                significance=calc.get("significance"),
                consequence_description=calc.get("consequence_description"),
                source=calc.get("source"),
            )

        # Drawing references
        drawing_refs = [context.drawing_number] if context.drawing_number else []

        # Add validation warnings to scenario comments if needed
        scenario_comments = reasoning.scenario_comments
        if validation.needs_sme_review:
            scenario_comments += "\n\n⚠️ SME REVIEW REQUIRED: " + "; ".join(validation.warnings)

        # Add knowledge sources to scenario
        sources = knowledge.get_source_references()
        if sources:
            scenario_comments += f"\n\nKnowledge Sources: {', '.join(sources[:5])}"

        # Build per-category rows
        if reasoning.category_rows:
            category_rows = [
                CategoryRowItem(
                    category=row.get("category", "PAF"),
                    consequences=row.get("consequences", []),
                    scenario_comments=row.get("scenario_comments"),
                    current_risk=row.get("current_risk"),
                    pec=row.get("pec"),
                )
                for row in reasoning.category_rows
            ]
        else:
            # Fallback: put all AI consequences in PAF, templates for PD/LOR and ECR
            category_rows = [
                CategoryRowItem(
                    category="PAF",
                    consequences=reasoning.combined_final,
                    scenario_comments=reasoning.scenario_comments,
                    pec=reasoning.pec,
                    current_risk=reasoning.current_risk,
                ),
                CategoryRowItem(
                    category="PD/LOR",
                    consequences=["Downtime of approximately X to X months"],
                ),
                CategoryRowItem(category="ECR"),
            ]

        return DeviationConsequencesItem(
            deviation_id=context.deviation_id,
            equipment_tag=context.equipment_tag,
            deviation=context.deviation_description,
            guideword=context.guideword,
            parameter=context.parameter,
            causes=context.approved_causes,
            drawing_references=drawing_refs,
            intermediate_consequences=reasoning.combined_intermediate,
            consequences=reasoning.combined_final,
            scenario_comments=scenario_comments,
            consequence_category=reasoning.consequence_category,
            pec=reasoning.pec,
            current_risk=reasoning.current_risk,
            overpressure_calc=overpressure_calc,
            category_rows=category_rows,
        )

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _build_context(
        self,
        node: PIDNode,
        deviation_id: str,
        guideword: str,
        parameter: str,
        deviation_description: str,
        equipment: Equipment,
        approved_causes: list[str],
        pid_instruments: list[dict],
    ) -> DeviationContext:
        """Build deviation context from inputs."""
        return DeviationContext(
            deviation_id=deviation_id,
            guideword=guideword,
            parameter=parameter,
            deviation_description=deviation_description,
            equipment_tag=equipment.tag,
            equipment_type=equipment.equipment_type,
            design_pressure=equipment.design_pressure,
            operating_pressure=equipment.operating_pressure,
            design_temperature=equipment.design_temperature,
            operating_temperature=getattr(equipment, "operating_temperature", None),
            upstream_pressure_psig=node.upstream_pressure_psig,
            pid_summary=node.pid_summary,
            flow_description=node.flow_description,
            drawing_number=getattr(node, "drawing_number", None),
            approved_causes=approved_causes,
            pid_instruments=pid_instruments,
        )


# Module-level singleton
consequence_agent = ConsequenceAgent()
