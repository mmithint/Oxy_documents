"""
Mitigation Agent — Knowledge-Driven Multi-Step Reasoning

A scalable agent that generates safeguards/mitigations for HAZOP deviations
using knowledge documents as the single source of truth.

Architecture:
    Step 1: RETRIEVE  — Maximum-coverage RAG per instrument type + CME table
    Step 2: REASON    — LLM classifies each instrument against CME table
    Step 3: VALIDATE  — Ensure every instrument has a row, check mandatory safeguards
    Step 4: OUTPUT    — Return structured DeviationSafeguardsItem with probability + RL

Key Principle:
    Every CME classification must come from the knowledge documents.
    One row per instrument — no lists/bullets inside cells.
    Probability auto-calculated from mitigation count.

Knowledge Sources:
    - HSE Risk Assessment document (CME table, PR-1 through PR-5 classifications)
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from app.services.claude_service import claude_service
from app.services.knowledge_service import knowledge_service
from app.models.pid_models import PIDNode, Equipment
from app.models.api_models import DeviationSafeguardsItem, SafeguardReviewItem
from app.core.config import get_settings

settings = get_settings()


# =============================================================================
# OOG RISK MATRIX — Hardcoded (no RAG, no LLM)
# Rows = Consequence Level (1–5), Cols = Probability (1–5)
# =============================================================================

OOG_RISK_MATRIX: dict[tuple[int, int], str] = {
    (5, 1): "B", (5, 2): "C", (5, 3): "D", (5, 4): "E", (5, 5): "E",
    (4, 1): "B", (4, 2): "C", (4, 3): "D", (4, 4): "D", (4, 5): "E",
    (3, 1): "A", (3, 2): "B", (3, 3): "C", (3, 4): "D", (3, 5): "D",
    (2, 1): "A", (2, 2): "B", (2, 3): "B", (2, 4): "C", (2, 5): "D",
    (1, 1): "A", (1, 2): "A", (1, 3): "B", (1, 4): "B", (1, 5): "C",
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class MitigationContext:
    """All context needed for safeguard/mitigation analysis."""
    deviation_id: str
    guideword: str
    parameter: str
    deviation_description: str

    # Equipment info
    equipment_tag: str
    equipment_type: str

    # SME-approved items
    approved_causes: list[str] = field(default_factory=list)
    approved_consequences: list[str] = field(default_factory=list)
    intermediate_consequences: list[str] = field(default_factory=list)

    # Scenario context
    scenario_comments: Optional[str] = None
    consequence_category: Optional[str] = None
    current_risk: Optional[str] = None   # e.g. "C5", "D4" — from approved consequences

    # P&ID data
    pid_instruments: list[dict] = field(default_factory=list)
    drawing_number: Optional[str] = None


@dataclass
class KnowledgeChunk:
    """A single knowledge chunk with metadata."""
    content: str
    source_document: str
    page: Optional[int] = None
    relevance_score: float = 0.0
    query_used: str = ""


@dataclass
class SafeguardKnowledge:
    """All knowledge retrieved for safeguard classification."""
    cme_table_chunks: list[KnowledgeChunk] = field(default_factory=list)
    per_instrument_chunks: dict[str, list[KnowledgeChunk]] = field(default_factory=dict)
    consequence_barrier_chunks: list[KnowledgeChunk] = field(default_factory=list)

    def get_full_context(self) -> str:
        """Combine all chunks into a single context string for LLM."""
        sections = []

        if self.cme_table_chunks:
            sections.append("## CME TABLE (PR Classifications)")
            for chunk in self.cme_table_chunks:
                sections.append(self._format_chunk(chunk))

        if self.per_instrument_chunks:
            sections.append("\n## INSTRUMENT-SPECIFIC KNOWLEDGE")
            for instrument_type, chunks in self.per_instrument_chunks.items():
                sections.append(f"\n### Instrument Type: {instrument_type}")
                for chunk in chunks:
                    sections.append(self._format_chunk(chunk))

        if self.consequence_barrier_chunks:
            sections.append("\n## CONSEQUENCE-BARRIER LOOKUP")
            for chunk in self.consequence_barrier_chunks:
                sections.append(self._format_chunk(chunk))

        return "\n".join(sections)

    def _format_chunk(self, chunk: KnowledgeChunk) -> str:
        page_info = f", Page {chunk.page}" if chunk.page else ""
        return f"[Source: {chunk.source_document}{page_info}]\n{chunk.content}\n"


@dataclass
class SafeguardReasoningResult:
    """Result from the reasoning step."""
    safeguards: list[dict] = field(default_factory=list)
    mandatory_safeguards_added: list[str] = field(default_factory=list)
    reasoning_trace: list[str] = field(default_factory=list)


@dataclass
class SafeguardValidationResult:
    """Result from the validation step."""
    is_valid: bool = True
    warnings: list[str] = field(default_factory=list)
    needs_sme_review: bool = False


# =============================================================================
# MITIGATION AGENT
# =============================================================================

class MitigationAgent:
    """
    Knowledge-driven agent for generating HAZOP safeguard classifications.

    All CME names and PR classifications come from knowledge documents.
    One row per instrument — no bullet points or lists inside cells.
    """

    def __init__(self):
        self.knowledge = knowledge_service

    # -------------------------------------------------------------------------
    # MAIN ENTRY POINT
    # -------------------------------------------------------------------------

    async def generate_safeguards(
        self,
        node: PIDNode,
        deviation_id: str,
        guideword: str,
        parameter: str,
        deviation_description: str,
        equipment: Equipment,
        approved_causes: list[str],
        approved_consequences: list[str],
        intermediate_consequences: list[str],
        scenario_comments: Optional[str],
        consequence_category: Optional[str],
        pid_instruments: list[dict],
        current_risk: Optional[str] = None,
    ) -> DeviationSafeguardsItem:
        """
        Generate safeguards using multi-step knowledge-driven reasoning.

        Returns:
            DeviationSafeguardsItem with one SafeguardReviewItem per instrument,
            plus probability and rl auto-calculated.
        """
        context = MitigationContext(
            deviation_id=deviation_id,
            guideword=guideword,
            parameter=parameter,
            deviation_description=deviation_description,
            equipment_tag=equipment.tag,
            equipment_type=equipment.equipment_type,
            approved_causes=approved_causes,
            approved_consequences=approved_consequences,
            intermediate_consequences=intermediate_consequences,
            scenario_comments=scenario_comments,
            consequence_category=consequence_category,
            current_risk=current_risk,
            pid_instruments=pid_instruments,
            drawing_number=getattr(node, "drawing_number", None),
        )

        print(f"[MitigationAgent] Step 1/4 RETRIEVE — {context.equipment_tag}: {context.deviation_description}")
        knowledge = await self._step_retrieve(context)
        print(f"[MitigationAgent] Step 2/4 REASON — {len(context.pid_instruments)} instruments")
        reasoning = await self._step_reason(context, knowledge)
        print(f"[MitigationAgent] Step 3/4 VALIDATE — {len(reasoning.safeguards)} safeguards generated")
        validation = self._step_validate(context, reasoning)
        print(f"[MitigationAgent] Step 4/4 OUTPUT — warnings={len(validation.warnings)}")
        return self._step_output(context, knowledge, reasoning, validation)

    # -------------------------------------------------------------------------
    # STEP 1: RETRIEVE — Maximum coverage RAG
    # -------------------------------------------------------------------------

    async def _step_retrieve(self, context: MitigationContext) -> SafeguardKnowledge:
        """
        Three-layer retrieval for maximum CME classification coverage.

        Layer 1: Full CME table (one broad query)
        Layer 2: Per unique instrument type (one query each)
        Layer 3: Consequence-driven barrier lookup (one query)
        """
        knowledge = SafeguardKnowledge()

        # Layer 1 — Full CME table query
        cme_query = (
            "CME table PR-1 PR-2 PR-3 PR-4 PR-5 classification description "
            "applicability safety instrumented system HSE risk assessment"
        )
        cme_chunks = await self._retrieve_chunks(cme_query, limit=8)
        knowledge.cme_table_chunks.extend(cme_chunks)

        # Layer 2 — Per unique instrument type
        seen_types: set[str] = set()
        for inst in context.pid_instruments:
            instrument_type = inst.get("instrument_type", "")
            if not instrument_type or instrument_type in seen_types:
                continue
            seen_types.add(instrument_type)

            inst_query = self._build_instrument_query(instrument_type)
            inst_chunks = await self._retrieve_chunks(inst_query, limit=5)
            if inst_chunks:
                knowledge.per_instrument_chunks[instrument_type] = inst_chunks

        # Layer 3 — Consequence-driven barrier lookup
        if context.approved_consequences or context.intermediate_consequences:
            consequence_text = " ".join(
                (context.approved_consequences or []) +
                (context.intermediate_consequences or [])
            )[:300]
            barrier_query = (
                f"{consequence_text} safeguard barrier prevent mitigate "
                "CME protection PR classification"
            )
            barrier_chunks = await self._retrieve_chunks(barrier_query, limit=5)
            knowledge.consequence_barrier_chunks.extend(barrier_chunks)

        return knowledge

    def _build_instrument_query(self, instrument_type: str) -> str:
        """Build a targeted CME query for a specific instrument type."""
        type_lower = instrument_type.lower()

        if "pressure switch high" in type_lower or "pshh" in type_lower or "psh" in type_lower:
            return (
                "pressure switch high high SIS safety instrumented CME PR classification "
                "instrumented protective system IPS"
            )
        elif "pressure safety valve" in type_lower or "psv" in type_lower or "prv" in type_lower:
            return (
                "pressure safety valve passive relief protection CME PR "
                "mechanical protection device"
            )
        elif "gas detector" in type_lower or "gas detection" in type_lower:
            return (
                "gas detector fire gas detection CME PR classification "
                "detection system alarm"
            )
        elif "level switch" in type_lower or "lshh" in type_lower or "lsll" in type_lower:
            return (
                "level switch high high low low SIS safety instrumented "
                "CME PR classification IPS"
            )
        elif "emergency shutdown" in type_lower or "esdv" in type_lower or "esv" in type_lower:
            return (
                "emergency shutdown valve ESDV ESD isolation CME PR classification "
                "safety instrumented system"
            )
        elif "blowdown" in type_lower or "bdv" in type_lower:
            return (
                "blowdown valve BDV depressurisation CME PR classification "
                "emergency depressurization"
            )
        elif "temperature switch" in type_lower or "tshh" in type_lower:
            return (
                "temperature switch high high safety instrumented CME PR "
                "classification IPS"
            )
        elif "flow control" in type_lower or "fcv" in type_lower:
            return (
                "flow control valve FCV CME PR classification active protection "
                "control system"
            )
        elif "deluge" in type_lower:
            return (
                "deluge system fire suppression CME PR classification "
                "TSE thermal safeguarding equipment"
            )
        elif "fire detector" in type_lower:
            return (
                "fire detector detection system CME PR classification "
                "fire gas detection"
            )
        else:
            return (
                f"{instrument_type} CME PR classification safety protection "
                "HSE risk assessment mitigation"
            )

    async def _retrieve_chunks(
        self,
        query: str,
        limit: int = 5,
        document_type: Optional[str] = None,
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
    # STEP 2: REASON — Single LLM call with full context
    # -------------------------------------------------------------------------

    async def _step_reason(
        self,
        context: MitigationContext,
        knowledge: SafeguardKnowledge,
    ) -> SafeguardReasoningResult:
        """
        Classify each instrument against the CME table using a single LLM call.
        """
        system_prompt = self._get_system_prompt()
        user_prompt = self._build_reasoning_prompt(context, knowledge)

        try:
            result = await claude_service.call_llm_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=3000,
                temperature=0.1,
            )

            return SafeguardReasoningResult(
                safeguards=result.get("safeguards", []),
                mandatory_safeguards_added=result.get("mandatory_safeguards_added", []),
                reasoning_trace=result.get("reasoning_trace", []),
            )

        except Exception as e:
            print(f"[MitigationAgent] Reasoning failed: {e}")
            # Fallback: create minimal entries from pid_instruments
            fallback_safeguards = []
            for inst in context.pid_instruments:
                fallback_safeguards.append({
                    "instrument_tag": inst.get("tag", ""),
                    "description": (
                        f"{inst.get('instrument_type', 'Instrument')} ({inst.get('tag', '')}) "
                        f"provides protection against {context.guideword} {context.parameter}"
                    ),
                    "pr_classification": "Other",
                    "mitigation_type": None,
                    "pid_reference": inst.get("pid_reference"),
                    "control_category": None,
                    "cme_name": None,
                    "cme_id": None,
                })
            return SafeguardReasoningResult(
                safeguards=fallback_safeguards,
                reasoning_trace=[f"Fallback due to error: {str(e)}"],
            )

    def _get_system_prompt(self) -> str:
        return (
            "You are a senior HAZOP engineer specialising in safety barrier analysis. "
            "Classify each instrument below against the CME table from the HSE Risk Assessment "
            "document. Use ONLY the document — do not use your own training knowledge. "
            "Each instrument must produce exactly one output row. No bullet points or lists "
            "inside any field — all values must be plain single-line strings."
        )

    def _build_reasoning_prompt(
        self,
        context: MitigationContext,
        knowledge: SafeguardKnowledge,
    ) -> str:
        """Build the full reasoning prompt combining all context."""

        prompt = f"""## DEVIATION CONTEXT
Equipment: {context.equipment_tag} ({context.equipment_type})
Deviation: {context.guideword} {context.parameter}
Description: {context.deviation_description}
"""

        # Approved causes
        if context.approved_causes:
            prompt += "\n## APPROVED CAUSES\n"
            for i, cause in enumerate(context.approved_causes, 1):
                prompt += f"{i}. {cause}\n"

        # Consequence chain
        prompt += "\n## CONSEQUENCE CHAIN\n"
        if context.intermediate_consequences:
            prompt += "Intermediate Consequences:\n"
            for ic in context.intermediate_consequences:
                prompt += f"- {ic}\n"
        if context.approved_consequences:
            prompt += "Final Consequences:\n"
            for fc in context.approved_consequences:
                prompt += f"- {fc}\n"
        if context.scenario_comments:
            prompt += f"Scenario Comments: {context.scenario_comments}\n"

        # P&ID instruments
        prompt += "\n## P&ID INSTRUMENTS\n"
        if context.pid_instruments:
            for i, inst in enumerate(context.pid_instruments, 1):
                tag = inst.get("tag", f"INST-{i}")
                itype = inst.get("instrument_type", "Unknown")
                pid_ref = inst.get("pid_reference", "")
                pid_str = f" [P&ID: {pid_ref}]" if pid_ref else ""
                prompt += f"{i}. {tag} ({itype}){pid_str}\n"
        else:
            prompt += "No P&ID instruments matched.\n"

        # CME Knowledge
        prompt += "\n## CME KNOWLEDGE (Use ONLY this to classify)\n"
        prompt += knowledge.get_full_context()

        # Mandatory rules
        consequence_text = " ".join(
            (context.approved_consequences or []) +
            (context.intermediate_consequences or [])
        ).lower()
        prompt += "\n## MANDATORY RULES\n"
        prompt += (
            "- If consequence involves LOC / hydrocarbon release / vessel rupture: "
            "add a Gas Detection System row\n"
            "- If consequence involves Jet Fire: add a TSE / Deluge row\n"
        )

        # Output format
        prompt += """
## OUTPUT FORMAT
Return a JSON object with this exact structure.
All string fields must be plain single-line strings (no bullet points, no newlines).

{
  "reasoning_trace": [
    "Step 1: Examined instrument PSHH-1010...",
    "Step 2: Found PR-1 classification in CME table..."
  ],
  "mandatory_safeguards_added": ["Gas Detection System"],
  "safeguards": [
    {
      "instrument_tag": "PSHH-1010",
      "description": "PSHH-1010 closes BSDV on high-high pressure signal preventing overpressure escalation",
      "pr_classification": "PR-1",
      "mitigation_type": "CME",
      "pid_reference": "4020",
      "control_category": "Prevention",
      "cme_name": "Safety Instrumented System (SIS) / Instrumented Protective System (IPS)",
      "cme_id": "PR-1"
    }
  ]
}

## STRICT OUTPUT RULES
- `description`: one plain sentence, no bullet points, no line breaks, no dashes at start
- `cme_name`: exact text from the CME Description column in the document, no truncation
- `pr_classification`: exactly "PR-1", "PR-2", "PR-3", "PR-4", "PR-5" from CME ID column, or "Other"
- `cme_id`: same value as pr_classification
- `mitigation_type`: "CME" or "KME" from the document
- `control_category`: "Prevention", "Detection", or "Mitigation"
- If an instrument doesn't match any CME row: pr_classification="Other", cme_name=null
- One safeguard object per instrument — never combine multiple instruments into one row
- Process ALL instruments listed above, one row each
"""

        return prompt

    # -------------------------------------------------------------------------
    # STEP 3: VALIDATE
    # -------------------------------------------------------------------------

    def _step_validate(
        self,
        context: MitigationContext,
        reasoning: SafeguardReasoningResult,
    ) -> SafeguardValidationResult:
        """
        Validate that output meets quality requirements:
        - Every input instrument has a corresponding output row
        - Mandatory safeguards present for LOC/fire scenarios
        - No bullet chars or newlines in string fields
        - pr_classification matches pattern
        """
        validation = SafeguardValidationResult()

        # Clean all string fields
        for sg in reasoning.safeguards:
            for field_name in ["description", "cme_name"]:
                val = sg.get(field_name)
                if val and isinstance(val, str):
                    cleaned = self._clean_string_field(val)
                    sg[field_name] = cleaned

        # Check every input instrument has an output row
        output_tags = {sg.get("instrument_tag", "").strip() for sg in reasoning.safeguards}
        for inst in context.pid_instruments:
            tag = inst.get("tag", "")
            if tag and tag not in output_tags:
                validation.warnings.append(
                    f"Input instrument {tag} has no corresponding output row"
                )
                # Auto-add missing instrument with minimal entry
                reasoning.safeguards.append({
                    "instrument_tag": tag,
                    "description": (
                        f"{inst.get('instrument_type', 'Instrument')} ({tag}) "
                        f"provides protection for {context.deviation_description}"
                    ),
                    "pr_classification": "Other",
                    "mitigation_type": None,
                    "pid_reference": inst.get("pid_reference"),
                    "control_category": None,
                    "cme_name": None,
                    "cme_id": None,
                })

        # Check mandatory safeguards
        consequence_text = " ".join(
            (context.approved_consequences or []) +
            (context.intermediate_consequences or [])
        ).lower()
        output_tags_after = {sg.get("instrument_tag", "").lower() for sg in reasoning.safeguards}
        all_descriptions = " ".join(
            sg.get("description", "").lower() for sg in reasoning.safeguards
        )

        loc_keywords = ["leak", "release", "rupture", "loc", "loss of containment", "hydrocarbon"]
        if any(kw in consequence_text for kw in loc_keywords):
            has_gas_detection = any(
                "gas" in desc and ("detect" in desc or "system" in desc)
                for desc in (sg.get("description", "").lower() for sg in reasoning.safeguards)
            ) or any("gas detect" in tag for tag in output_tags_after)
            if not has_gas_detection:
                validation.warnings.append(
                    "LOC scenario: Gas Detection System row should be present"
                )

        fire_keywords = ["jet fire", "pool fire", "flash fire", "fire"]
        if any(kw in consequence_text for kw in fire_keywords):
            has_deluge = any(
                "deluge" in desc or "tse" in desc
                for desc in (sg.get("description", "").lower() for sg in reasoning.safeguards)
            )
            if not has_deluge:
                validation.warnings.append(
                    "Fire scenario: TSE / Deluge row should be present"
                )

        # Validate pr_classification format
        pr_pattern = re.compile(r"^PR-\d+$")
        for sg in reasoning.safeguards:
            pr = sg.get("pr_classification", "Other")
            if pr and pr != "Other" and not pr_pattern.match(pr):
                # Try to fix common issues
                match = re.search(r"PR-\d+", pr)
                if match:
                    sg["pr_classification"] = match.group()
                    sg["cme_id"] = match.group()
                else:
                    sg["pr_classification"] = "Other"
                    validation.warnings.append(
                        f"Invalid pr_classification '{pr}' corrected to 'Other'"
                    )

        if validation.warnings:
            validation.needs_sme_review = True

        return validation

    def _clean_string_field(self, value: str) -> str:
        """Remove bullet chars, leading dashes, and newlines from a string field."""
        # Remove newlines and carriage returns
        cleaned = value.replace("\n", " ").replace("\r", " ")
        # Remove bullet chars at start
        cleaned = re.sub(r"^[\s•\-\*]+", "", cleaned).strip()
        # Collapse multiple spaces
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned

    # -------------------------------------------------------------------------
    # STEP 4: OUTPUT
    # -------------------------------------------------------------------------

    def _step_output(
        self,
        context: MitigationContext,
        knowledge: SafeguardKnowledge,
        reasoning: SafeguardReasoningResult,
        validation: SafeguardValidationResult,
    ) -> DeviationSafeguardsItem:
        """Build the final output structure with probability and RL."""

        # Build SafeguardReviewItem list
        safeguard_items = [
            SafeguardReviewItem(
                instrument_tag=sg.get("instrument_tag", ""),
                description=sg.get("description", ""),
                pr_classification=sg.get("pr_classification", "Other"),
                mitigation_type=sg.get("mitigation_type"),
                pid_reference=sg.get("pid_reference"),
                control_category=sg.get("control_category"),
                cme_name=sg.get("cme_name"),
                cme_id=sg.get("cme_id"),
            )
            for sg in reasoning.safeguards
        ]

        # Calculate probability = max(1, 5 - count_of_safeguards)
        probability = max(1, 5 - len(safeguard_items))

        # Calculate RL from OOG Risk Matrix
        rl = self._calculate_rl(probability, context.current_risk)

        # Drawing references
        drawing_refs = [context.drawing_number] if context.drawing_number else []

        # Append validation warnings to scenario comments if needed
        scenario_comments = context.scenario_comments
        if validation.needs_sme_review and validation.warnings:
            warning_str = "; ".join(validation.warnings)
            if scenario_comments:
                scenario_comments = f"{scenario_comments}\n\n⚠️ SME REVIEW: {warning_str}"
            else:
                scenario_comments = f"⚠️ SME REVIEW: {warning_str}"

        return DeviationSafeguardsItem(
            deviation_id=context.deviation_id,
            equipment_tag=context.equipment_tag,
            deviation=context.deviation_description,
            guideword=context.guideword,
            parameter=context.parameter,
            causes=context.approved_causes,
            drawing_references=drawing_refs,
            intermediate_consequences=context.intermediate_consequences,
            consequences=context.approved_consequences,
            scenario_comments=scenario_comments,
            consequence_category=context.consequence_category,
            current_risk=context.current_risk,
            safeguards=safeguard_items,
            probability=probability,
            rl=rl,
        )

    def _calculate_rl(self, probability: int, current_risk: Optional[str]) -> Optional[str]:
        """
        Calculate residual risk letter from OOG Risk Matrix.
        current_risk: e.g. "C5" → consequence_level = 5
        probability: 1–5
        """
        if not current_risk:
            return None
        try:
            # Extract trailing digit as consequence level (e.g. "C5" → 5)
            consequence_level = int(current_risk[-1])
            return OOG_RISK_MATRIX.get((consequence_level, probability), "A")
        except (ValueError, IndexError):
            return None


# Module-level singleton
mitigation_agent = MitigationAgent()
