"""
HAZOP Generator — Main Orchestrator

This is the central service that orchestrates the full HAZOP generation pipeline.
It connects all modules in the correct sequence:

Pipeline:
  1. Validated PIDNode (equipment + instruments confirmed by SME)
       ↓
  2. Deviation Generator (rule-based) — creates all deviation rows
       ↓
  3. Knowledge Retrieval (RAG) — fetches relevant context from knowledge docs
       ↓
  4. LLM Enrichment — enhances causes/consequences using AI + knowledge context
       ↓
  5. Severity Estimation (LLM-suggested, SME-validated)
       ↓
  6. Risk Engine (deterministic) — matrix lookup for PAF, PD/LOR, ECR
       ↓
  7. Recommendations (LLM-generated if risk >= C)
       ↓
  8. HAZOP Report assembly — structured output ready for SME review

IMPORTANT:
  - Steps 2, 6 are 100% deterministic (no LLM)
  - Steps 3, 4, 5, 7 use LLM (constrained, structured JSON)
  - Final output is ALWAYS "draft" status — SME must approve
"""

import uuid
from datetime import datetime

from app.models.pid_models import PIDNode
from app.models.hazop_models import (
    Deviation, HAZOPReport, RiskAssessment, ReviewStatus,
)
from app.services.deviation_generator import deviation_generator
from app.services.risk_engine import risk_engine
from app.services.openai_service import openai_service
from app.services.knowledge_service import knowledge_service
from app.database.cosmos_client import cosmos_client


class HAZOPGeneratorService:
    """
    Orchestrates the full HAZOP report generation pipeline.

    Usage:
        generator = HAZOPGeneratorService()
        report = await generator.generate_full_hazop(node)
    """

    def __init__(self, use_llm: bool = True):
        """
        Args:
            use_llm: If False, skip LLM enrichment (useful for testing
                     or when Azure OpenAI is not available).
                     Ontology causes/consequences will be used as-is.
        """
        self.use_llm = use_llm

    # ------------------------------------------------------------------
    # MAIN PIPELINE
    # ------------------------------------------------------------------

    async def generate_full_hazop(
        self,
        node: PIDNode,
        include_recommendations: bool = True,
        selected_deviation_types: list[str] | None = None,
        approved_causes: dict | None = None,
        approved_consequences: dict | None = None,
        approved_safeguards: dict | None = None,
    ) -> HAZOPReport:
        """
        Generate a complete HAZOP report for a validated node.

        Args:
            node: Validated PIDNode with confirmed equipment and instruments
            include_recommendations: Whether to generate recommendations for high-risk items
            selected_deviation_types: If provided, only generate these deviation types.
            approved_causes: If provided, dict of deviation_id -> {causes: [...], ...}
                            from SME review. LLM will not overwrite these causes.
            approved_consequences: If provided, dict of deviation_id -> consequence fields
                            from SME review. LLM will not overwrite these consequence fields.
            approved_safeguards: If provided, dict of deviation_id -> {safeguards: [...], ...}
                            from SME review. These replace placeholder safeguards.

        Returns:
            Complete HAZOPReport with all deviations, risk scores, and recommendations
        """
        report_id = str(uuid.uuid4())

        # ---- STEP 1: Generate deviations (rule-based) ----
        deviations = deviation_generator.generate_deviations_for_node(
            node, selected_deviation_types=selected_deviation_types,
        )

        # If we have approved causes, apply them to matching deviations
        if approved_causes:
            deviations = self._apply_approved_causes(deviations, approved_causes)

        # If we have approved consequences, apply them to matching deviations
        if approved_consequences:
            deviations = self._apply_approved_consequences(deviations, approved_consequences)

        # If we have approved safeguards, apply them to matching deviations
        if approved_safeguards:
            deviations = self._apply_approved_safeguards(deviations, approved_safeguards)

        # ---- STEP 2-6: Enrich each deviation ----
        enriched_deviations: list[Deviation] = []

        for deviation in deviations:
            # Skip cause generation if this deviation has SME-approved causes
            skip_causes = (
                approved_causes is not None
                and deviation.deviation_id in approved_causes
            )
            # Skip consequence generation if this deviation has SME-approved consequences
            skip_consequences = (
                approved_consequences is not None
                and deviation.deviation_id in approved_consequences
            )
            # Skip safeguard enrichment if this deviation has SME-approved safeguards
            skip_safeguards = (
                approved_safeguards is not None
                and deviation.deviation_id in approved_safeguards
            )
            enriched = await self._enrich_deviation(
                deviation=deviation,
                node=node,
                include_recommendations=include_recommendations,
                skip_causes=skip_causes,
                skip_consequences=skip_consequences,
                skip_safeguards=skip_safeguards,
            )
            enriched_deviations.append(enriched)

        # ---- STEP 7: Assemble report ----
        report = HAZOPReport(
            report_id=report_id,
            node_id=node.node_id,
            node_name=node.node_name,
            system=node.system,
            deviations=enriched_deviations,
            status=ReviewStatus.DRAFT,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            version=1,
        )

        # ---- STEP 8: Store in Cosmos DB ----
        await self._save_report(report)

        return report

    # ------------------------------------------------------------------
    # DEVIATION ENRICHMENT PIPELINE
    # ------------------------------------------------------------------

    async def _enrich_deviation(
        self,
        deviation: Deviation,
        node: PIDNode,
        include_recommendations: bool,
        skip_causes: bool = False,
        skip_consequences: bool = False,
        skip_safeguards: bool = False,
    ) -> Deviation:
        """
        Enrich a single deviation with LLM-generated content and risk scores.

        Pipeline per deviation:
          1. Retrieve relevant knowledge context (RAG)
          2. Enhance causes/consequences with LLM (skip causes if pre-approved)
          3. Estimate severity (LLM-suggested)
          4. Calculate risk (deterministic matrix lookup)
          5. Generate recommendations if risk is high
        """
        # Find the equipment for this deviation
        equipment = self._find_equipment(node, deviation.equipment_tag)
        equipment_type = equipment.equipment_type if equipment else "Unknown"
        design_pressure = equipment.design_pressure if equipment else None
        design_temperature = equipment.design_temperature if equipment else None

        # ---- RAG: Retrieve knowledge context ----
        knowledge_context = ""
        if self.use_llm:
            knowledge_context = await self._get_knowledge_context(
                equipment_type=equipment_type,
                deviation=deviation.deviation,
            )

        # ---- LLM: Enrich all HAZOP fields ----
        if self.use_llm:
            await self._enrich_all_fields(
                deviation=deviation,
                equipment_type=equipment_type,
                design_pressure=design_pressure,
                design_temperature=design_temperature,
                knowledge_context=knowledge_context,
                skip_causes=skip_causes,
                skip_consequences=skip_consequences,
                skip_safeguards=skip_safeguards,
                node=node,
            )

        # ---- LLM: Estimate severity ----
        severity_data = None
        if self.use_llm:
            severity_data = await self._estimate_severity(
                equipment_type=equipment_type,
                deviation=deviation,
                design_pressure=design_pressure,
                knowledge_context=knowledge_context,
            )

        # ---- DETERMINISTIC: Calculate risk ----
        deviation.risk = self._calculate_risk(
            deviation=deviation,
            severity_data=severity_data,
        )

        # ---- LLM: Generate recommendations if needed ----
        if include_recommendations and deviation.risk:
            if risk_engine.requires_recommendation(deviation.risk):
                await self._generate_recommendations(
                    deviation=deviation,
                    knowledge_context=knowledge_context,
                )

        deviation.updated_at = datetime.utcnow()
        return deviation

    # ------------------------------------------------------------------
    # STEP IMPLEMENTATIONS
    # ------------------------------------------------------------------

    async def _get_knowledge_context(
        self,
        equipment_type: str,
        deviation: str,
    ) -> str:
        """Retrieve relevant knowledge from vector store (full HAZOP context)."""
        try:
            context = await knowledge_service.retrieve_full_hazop_context(
                equipment_type=equipment_type,
                deviation=deviation,
                limit=5,
            )
            return context
        except Exception:
            # Knowledge retrieval failure should not block HAZOP generation
            return ""

    async def _enrich_all_fields(
        self,
        deviation: Deviation,
        equipment_type: str,
        design_pressure: float | None,
        design_temperature: float | None,
        knowledge_context: str,
        skip_causes: bool = False,
        skip_consequences: bool = False,
        skip_safeguards: bool = False,
        node: PIDNode | None = None,
    ) -> None:
        """Enrich ALL HAZOP fields using single expanded LLM call + RAG context.
        If skip_causes is True, causes are not overwritten (SME pre-approved).
        If skip_consequences is True, consequence fields are not overwritten (SME pre-approved).
        If skip_safeguards is True, safeguard enrichment is skipped (SME pre-approved)."""
        try:
            safeguard_descriptions = [
                sg.description for sg in deviation.safeguards
            ]

            # Build P&ID instrument and equipment data for LLM context
            instruments_data = None
            equipment_data = None
            if node:
                instruments_data = [
                    {
                        "tag": inst.tag,
                        "instrument_type": inst.instrument_type,
                        "setpoint": inst.setpoint,
                        "associated_equipment_tag": inst.associated_equipment_tag,
                    }
                    for inst in node.instruments
                ]
                equipment_data = [
                    {"tag": eq.tag, "equipment_type": eq.equipment_type}
                    for eq in node.equipment
                ]

            result = await openai_service.generate_deviation_content(
                equipment_type=equipment_type,
                equipment_tag=deviation.equipment_tag,
                deviation=deviation.deviation,
                design_pressure=design_pressure,
                design_temperature=design_temperature,
                existing_safeguards=safeguard_descriptions,
                knowledge_context=knowledge_context if knowledge_context else None,
                is_special_category=deviation.requires_mandatory_sme_review,
                node_instruments=instruments_data,
                node_equipment=equipment_data,
            )

            # Merge LLM causes with ontology causes (deduplicate)
            # Skip if causes were pre-approved by SME
            if not skip_causes:
                # Use only LLM (P&ID-grounded) causes, no ontology merge
                deviation.causes = result.get("causes", [])

            # Drawing references — prefer node.drawing_number over LLM guess
            if node and getattr(node, "drawing_number", None):
                deviation.drawing_references = [node.drawing_number]
            else:
                deviation.drawing_references = result.get("drawing_references", [])

            # Consequence fields — skip if SME pre-approved
            if not skip_consequences:
                # Intermediate consequences
                deviation.intermediate_consequences = result.get("intermediate_consequences", [])

                # Use only LLM (P&ID-grounded) consequences, no ontology merge
                deviation.consequences = result.get("consequences", [])

                # Scenario comments
                deviation.scenario_comments = result.get("scenario_comments")

                # Consequence category
                cat = result.get("consequence_category")
                if cat in ("PAF", "PD/LOR", "ECR"):
                    from app.models.hazop_models import ConsequenceCategory
                    deviation.consequence_category = ConsequenceCategory(cat)

                # PEC
                deviation.pec = result.get("personnel_exposure")

            # Enrich safeguards with control_category and cme_name from LLM
            # Skip if SME has pre-approved safeguards (they already have full classification)
            if not skip_safeguards:
                mitigation_details = result.get("mitigation_details", [])
                self._enrich_safeguards(deviation.safeguards, mitigation_details)

            # Recommendations (from expanded call, merged with later generation)
            llm_recs = result.get("recommendations", [])
            if llm_recs:
                deviation.recommendations = self._merge_lists(deviation.recommendations, llm_recs)

            # Responsibility
            deviation.responsibility = result.get("responsibility")

            # Planned residual risk
            residual = result.get("planned_residual_risk")
            if residual and isinstance(residual, dict):
                try:
                    deviation.planned_residual_risk = risk_engine.calculate_risk(
                        paf_consequence=residual.get("paf", {}).get("consequence", 2),
                        pd_lor_consequence=residual.get("pd_lor", {}).get("consequence", 2),
                        ecr_consequence=residual.get("ecr", {}).get("consequence", 1),
                        base_probability=residual.get("paf", {}).get("probability", 1),
                        safeguards=deviation.safeguards,
                    )
                except Exception:
                    pass

        except Exception:
            # LLM failure: clear ontology causes/consequences
            deviation.causes = []
            deviation.consequences = []

    def _enrich_safeguards(
        self,
        safeguards: list,
        mitigation_details: list[dict],
    ) -> None:
        """Enrich existing safeguards with control_category and cme_name from LLM output."""
        if not mitigation_details:
            return

        for detail in mitigation_details:
            name = detail.get("name", "")
            control_cat = detail.get("control_category")
            cme_name = name

            # Try to match by tag prefix in the name
            for sg in safeguards:
                if sg.instrument_tag in name or name in sg.instrument_tag:
                    if control_cat:
                        sg.control_category = control_cat
                    if cme_name:
                        sg.cme_name = cme_name
                    break

    async def _estimate_severity(
        self,
        equipment_type: str,
        deviation: Deviation,
        design_pressure: float | None,
        knowledge_context: str,
    ) -> dict | None:
        """Get LLM severity estimation for PAF, PD/LOR, ECR."""
        try:
            result = await openai_service.estimate_consequence_severity(
                equipment_type=equipment_type,
                deviation=deviation.deviation,
                consequences=deviation.consequences,
                design_pressure=design_pressure,
                knowledge_context=knowledge_context if knowledge_context else None,
            )
            return result
        except Exception:
            return None

    def _calculate_risk(
        self,
        deviation: Deviation,
        severity_data: dict | None,
    ) -> RiskAssessment:
        """
        Calculate risk using deterministic Risk Engine.

        Uses LLM-suggested severity if available, otherwise defaults.
        """
        # Extract severity values from LLM estimation
        if severity_data:
            paf_c = self._safe_severity(severity_data, "paf_severity")
            pd_lor_c = self._safe_severity(severity_data, "pd_lor_severity")
            ecr_c = self._safe_severity(severity_data, "ecr_severity")
            base_prob = self._safe_severity(severity_data, "base_probability")
        else:
            # Default conservative estimates when LLM is unavailable
            paf_c = 3      # Moderate
            pd_lor_c = 3   # Moderate
            ecr_c = 2      # Minor
            base_prob = 3   # Possible

        # DETERMINISTIC: Risk matrix lookup with safeguard probability reduction
        risk = risk_engine.calculate_risk(
            paf_consequence=paf_c,
            pd_lor_consequence=pd_lor_c,
            ecr_consequence=ecr_c,
            base_probability=base_prob,
            safeguards=deviation.safeguards,
        )

        return risk

    async def _generate_recommendations(
        self,
        deviation: Deviation,
        knowledge_context: str,
    ) -> None:
        """Generate recommendations for high-risk deviations (C, D, E)."""
        if not self.use_llm:
            deviation.recommendations = ["Manual review required — LLM unavailable"]
            return

        try:
            highest_risk = risk_engine.get_highest_risk_level(deviation.risk)
            safeguard_descriptions = [sg.description for sg in deviation.safeguards]

            recommendations = await openai_service.generate_recommendations(
                deviation=deviation.deviation,
                consequences=deviation.consequences,
                risk_level=highest_risk or "C",
                existing_safeguards=safeguard_descriptions,
                knowledge_context=knowledge_context if knowledge_context else None,
            )
            deviation.recommendations = recommendations
        except Exception:
            deviation.recommendations = ["Recommendation generation failed — manual review required"]

    # ------------------------------------------------------------------
    # REPORT GENERATION (Without LLM — Ontology Only)
    # ------------------------------------------------------------------

    async def generate_hazop_ontology_only(
        self,
        node: PIDNode,
        selected_deviation_types: list[str] | None = None,
        approved_causes: dict | None = None,
    ) -> HAZOPReport:
        """
        Generate HAZOP using only ontology data — no LLM calls.

        Useful for:
          - Quick draft when Azure OpenAI is unavailable
          - Testing the deterministic pipeline
          - Generating a skeleton for SME to fill manually

        Causes/consequences come from ontology only.
        Risk uses conservative default severity values.
        """
        report_id = str(uuid.uuid4())

        # Generate deviations (rule-based)
        deviations = deviation_generator.generate_deviations_for_node(
            node, selected_deviation_types=selected_deviation_types,
        )

        # Apply SME-approved causes if available
        if approved_causes:
            deviations = self._apply_approved_causes(deviations, approved_causes)

        # Calculate risk with default severity
        for deviation in deviations:
            deviation.risk = risk_engine.calculate_risk(
                paf_consequence=3,     # Default moderate
                pd_lor_consequence=3,
                ecr_consequence=2,
                base_probability=3,
                safeguards=deviation.safeguards,
            )

            if risk_engine.requires_recommendation(deviation.risk):
                deviation.recommendations = [
                    "AI-generated recommendations unavailable — manual review required"
                ]

        report = HAZOPReport(
            report_id=report_id,
            node_id=node.node_id,
            node_name=node.node_name,
            system=node.system,
            deviations=deviations,
            status=ReviewStatus.DRAFT,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            version=1,
        )

        await self._save_report(report)
        return report

    # ------------------------------------------------------------------
    # SINGLE DEVIATION REGENERATION
    # ------------------------------------------------------------------

    async def regenerate_deviation(
        self,
        report_id: str,
        deviation_id: str,
        node: PIDNode,
    ) -> Deviation | None:
        """
        Regenerate a single deviation (e.g., after SME requests revision).

        Fetches the existing report, finds the deviation, re-runs enrichment,
        and updates the report in Cosmos DB.
        """
        report_data = await cosmos_client.get_hazop_report(report_id)
        if not report_data:
            return None

        # Find the deviation
        target_deviation = None
        target_index = -1
        for i, dev_data in enumerate(report_data.get("deviations", [])):
            if dev_data.get("deviation_id") == deviation_id:
                target_deviation = Deviation(**dev_data)
                target_index = i
                break

        if target_deviation is None:
            return None

        # Re-enrich
        enriched = await self._enrich_deviation(
            deviation=target_deviation,
            node=node,
            include_recommendations=True,
        )
        enriched.status = ReviewStatus.DRAFT
        enriched.updated_at = datetime.utcnow()

        # Update in database
        report_data["deviations"][target_index] = enriched.model_dump(mode="json")
        report_data["updated_at"] = datetime.utcnow().isoformat()
        await cosmos_client.save_hazop_report(report_data)

        return enriched

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _apply_approved_causes(
        self,
        deviations: list[Deviation],
        approved_causes: dict,
    ) -> list[Deviation]:
        """
        Apply SME-approved causes to deviations.

        Matches by equipment_tag + deviation name since deviation_ids
        are regenerated each time.
        """
        # Build lookup by (equipment_tag, deviation_name)
        approved_lookup: dict[tuple[str, str], dict] = {}
        for _dev_id, data in approved_causes.items():
            key = (data.get("equipment_tag", ""), data.get("deviation", ""))
            approved_lookup[key] = data

        for dev in deviations:
            key = (dev.equipment_tag, dev.deviation)
            if key in approved_lookup:
                approved = approved_lookup[key]
                dev.causes = approved.get("causes", dev.causes)
                # Store the original approved deviation_id for skip_causes lookup
                # by adding the matching key to approved_causes with the new ID
                original_id = None
                for aid, adata in approved_causes.items():
                    if (adata.get("equipment_tag"), adata.get("deviation")) == key:
                        original_id = aid
                        break
                if original_id:
                    approved_causes[dev.deviation_id] = approved_causes[original_id]

        return deviations

    def _apply_approved_consequences(
        self,
        deviations: list[Deviation],
        approved_consequences: dict,
    ) -> list[Deviation]:
        """
        Apply SME-approved consequences to deviations before LLM enrichment.

        Mirrors _apply_approved_causes — matches by (equipment_tag, deviation_name)
        and populates all consequence fields from the approved data.
        """
        from app.models.hazop_models import ConsequenceCategory

        approved_lookup: dict[tuple[str, str], dict] = {}
        for _dev_id, data in approved_consequences.items():
            key = (data.get("equipment_tag", ""), data.get("deviation", ""))
            approved_lookup[key] = data

        for dev in deviations:
            key = (dev.equipment_tag, dev.deviation)
            if key in approved_lookup:
                approved = approved_lookup[key]
                dev.intermediate_consequences = approved.get("intermediate_consequences", dev.intermediate_consequences)
                dev.consequences = approved.get("consequences", dev.consequences)
                dev.scenario_comments = approved.get("scenario_comments", dev.scenario_comments)
                cat = approved.get("consequence_category")
                if cat in ("PAF", "PD/LOR", "ECR"):
                    dev.consequence_category = ConsequenceCategory(cat)
                dev.pec = approved.get("pec", dev.pec)
                # Register the new deviation_id in approved_consequences for skip lookup
                original_id = None
                for aid, adata in approved_consequences.items():
                    if (adata.get("equipment_tag"), adata.get("deviation")) == key:
                        original_id = aid
                        break
                if original_id:
                    approved_consequences[dev.deviation_id] = approved_consequences[original_id]

        return deviations

    def _apply_approved_safeguards(
        self,
        deviations: list[Deviation],
        approved_safeguards: dict,
    ) -> list[Deviation]:
        """
        Apply SME-approved safeguards to deviations before HAZOP generation.

        Matches by (equipment_tag, deviation_name) since deviation_ids are regenerated.
        Builds Safeguard objects from the stored SafeguardReviewItem dicts.
        """
        from app.models.hazop_models import Safeguard, PRClassification

        approved_lookup: dict[tuple[str, str], dict] = {}
        for _dev_id, data in approved_safeguards.items():
            key = (data.get("equipment_tag", ""), data.get("deviation", ""))
            approved_lookup[key] = data

        for dev in deviations:
            key = (dev.equipment_tag, dev.deviation)
            if key in approved_lookup:
                approved = approved_lookup[key]
                raw_safeguards = approved.get("safeguards", [])
                dev.safeguards = [
                    Safeguard(
                        instrument_tag=sg.get("instrument_tag", ""),
                        description=sg.get("description", ""),
                        pr_classification=PRClassification(sg.get("pr_classification", "Other"))
                            if sg.get("pr_classification") in [e.value for e in PRClassification]
                            else PRClassification.OTHER,
                        mitigation_type=sg.get("mitigation_type"),
                        pid_reference=sg.get("pid_reference"),
                        control_category=sg.get("control_category"),
                        cme_name=sg.get("cme_name"),
                        cme_id=sg.get("cme_id"),
                    )
                    for sg in raw_safeguards
                ]
                # Register new deviation_id for skip_safeguards lookup
                original_id = None
                for aid, adata in approved_safeguards.items():
                    if (adata.get("equipment_tag"), adata.get("deviation")) == key:
                        original_id = aid
                        break
                if original_id:
                    approved_safeguards[dev.deviation_id] = approved_safeguards[original_id]

        return deviations

    def _find_equipment(self, node: PIDNode, equipment_tag: str):
        """Find equipment in node by tag."""
        for eq in node.equipment:
            if eq.tag == equipment_tag:
                return eq
        return None

    def _merge_lists(self, base: list[str], additions: list[str]) -> list[str]:
        """
        Merge two lists of strings, avoiding near-duplicates.
        Keeps base items first, then adds unique LLM items.
        """
        merged = list(base)
        base_lower = {item.lower().strip() for item in base}

        for item in additions:
            item_lower = item.lower().strip()
            # Simple dedup: skip if substantially similar to existing
            is_duplicate = False
            for existing in base_lower:
                if self._is_similar(item_lower, existing):
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append(item)
                base_lower.add(item_lower)

        return merged

    def _is_similar(self, a: str, b: str) -> bool:
        """Simple similarity check — true if strings share >70% words."""
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return False
        overlap = len(words_a & words_b)
        smaller = min(len(words_a), len(words_b))
        return (overlap / smaller) > 0.7 if smaller > 0 else False

    def _safe_severity(self, severity_data: dict, key: str) -> int:
        """Safely extract severity value from LLM response with fallback."""
        try:
            entry = severity_data.get(key, {})
            if isinstance(entry, dict):
                value = entry.get("value", 3)
            else:
                value = entry
            return max(1, min(5, int(value)))
        except (ValueError, TypeError):
            return 3  # Default moderate if parsing fails

    async def _save_report(self, report: HAZOPReport) -> None:
        """Save report to Cosmos DB."""
        try:
            report_data = report.model_dump(mode="json")
            await cosmos_client.save_hazop_report(report_data)
        except Exception:
            # Storage failure should not crash generation
            pass


# ------------------------------------------------------------------
# Module-level instances
# ------------------------------------------------------------------

# Full pipeline with LLM
hazop_generator = HAZOPGeneratorService(use_llm=True)

# Ontology-only pipeline (no LLM dependency)
hazop_generator_offline = HAZOPGeneratorService(use_llm=False)
