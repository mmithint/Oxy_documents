"""
Deviation Generator — Rule-Based HAZOP Deviation Creation

Takes a validated PIDNode (with confirmed equipment and instruments)
and generates all applicable HAZOP deviation rows using the Equipment Ontology.

This module is 100% deterministic. No LLM involved.

Flow:
  PIDNode → For each equipment → Lookup ontology → Generate deviation rows
  → Attach detected safeguards → Return structured deviations

The output is a list of Deviation objects with:
  - Deviation definition (guideword + parameter)
  - Typical causes (from ontology — LLM will enrich later)
  - Typical consequences (from ontology — LLM will enrich later)
  - Detected safeguards (matched from node instruments)
  - Status = "draft" (pending SME review)
"""

import uuid
from datetime import datetime

from app.models.pid_models import PIDNode, Equipment, Instrument
from app.models.hazop_models import (
    Deviation, Safeguard, Guideword, DeviationParameter,
    ReviewStatus, PRClassification,
)
from app.services.ontology_engine import get_deviations_for_equipment, get_equipment_ontology
from app.services.safeguard_classifier import classify_instrument, match_safeguards_to_equipment


class DeviationGeneratorService:
    """
    Generates HAZOP deviations for a validated node.
    Uses ontology for deviation logic and instrument matching for safeguards.
    """

    def generate_deviations_for_node(
        self,
        node: PIDNode,
        selected_deviation_types: list[str] | None = None,
    ) -> list[Deviation]:
        """
        Generate all applicable deviations for every equipment in the node.

        Args:
            node: Validated PIDNode with confirmed equipment and instruments
            selected_deviation_types: If provided, only generate these deviation types.
                                     If None, generate all 14 standard types.

        Returns:
            List of Deviation objects ready for LLM enrichment and risk scoring
        """
        all_deviations: list[Deviation] = []

        for equipment in node.equipment:
            equipment_deviations = self._generate_for_equipment(
                equipment=equipment,
                node=node,
                selected_deviation_types=selected_deviation_types,
            )
            all_deviations.extend(equipment_deviations)

        return all_deviations

    def _generate_for_equipment(
        self,
        equipment: Equipment,
        node: PIDNode,
        selected_deviation_types: list[str] | None = None,
    ) -> list[Deviation]:
        """Generate all deviations for a single piece of equipment."""
        deviations: list[Deviation] = []

        # Get standard deviations (filtered by selection if provided)
        ontology_deviations = get_deviations_for_equipment(
            equipment.equipment_type,
            selected_deviation_types=selected_deviation_types,
        )

        if not ontology_deviations:
            # Unknown equipment type — create a minimal generic deviation
            deviations.append(self._create_generic_deviation(equipment, node))
            return deviations

        # Find safeguards associated with this equipment
        matched_safeguards = match_safeguards_to_equipment(
            equipment=equipment,
            instruments=node.instruments,
        )

        for dev_data in ontology_deviations:
            deviation = self._create_deviation_from_ontology(
                equipment=equipment,
                node=node,
                deviation_data=dev_data,
                matched_safeguards=matched_safeguards,
            )

            # Flag LLM-generated categories for mandatory SME review
            if dev_data.get("llm_generated", False):
                deviation.requires_mandatory_sme_review = True
                deviation.status = ReviewStatus.PENDING_REVIEW

            deviations.append(deviation)

        return deviations

    def _create_deviation_from_ontology(
        self,
        equipment: Equipment,
        node: PIDNode,
        deviation_data: dict,
        matched_safeguards: list[Safeguard],
    ) -> Deviation:
        """Create a single Deviation from ontology data."""

        # Determine guideword and parameter
        guideword = deviation_data.get("guideword", Guideword.OTHER)
        parameter = deviation_data.get("parameter", DeviationParameter.PRESSURE)
        deviation_name = deviation_data.get("deviation_name", "Unknown Deviation")

        # For additional deviations (corrosion, leak) that don't have guideword/parameter
        if "guideword" not in deviation_data:
            guideword = Guideword.OTHER
            parameter = DeviationParameter.COMPOSITION

        # Filter safeguards relevant to this deviation
        expected_tags = deviation_data.get("expected_safeguards", [])
        relevant_safeguards = self._filter_relevant_safeguards(
            matched_safeguards=matched_safeguards,
            expected_safeguard_types=expected_tags,
        )

        return Deviation(
            deviation_id=str(uuid.uuid4()),
            node_id=node.node_id,
            equipment_tag=equipment.tag,
            guideword=guideword,
            parameter=parameter,
            deviation=deviation_name,
            causes=deviation_data.get("typical_causes", []),
            consequences=deviation_data.get("typical_consequences", []),
            safeguards=relevant_safeguards,
            status=ReviewStatus.DRAFT,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            generated_by="ai_draft",
        )

    def _filter_relevant_safeguards(
        self,
        matched_safeguards: list[Safeguard],
        expected_safeguard_types: list[str],
    ) -> list[Safeguard]:
        """
        Filter matched safeguards to only include those relevant to this deviation.

        Matches by checking if the instrument tag prefix or type name
        appears in the expected safeguards list from the ontology.
        """
        if not expected_safeguard_types:
            return matched_safeguards  # Return all if no filter specified

        relevant = []
        for safeguard in matched_safeguards:
            tag_upper = safeguard.instrument_tag.upper()
            desc_upper = safeguard.description.upper()

            for expected in expected_safeguard_types:
                expected_upper = expected.upper()
                # Match by tag prefix (e.g., "PSHH" matches "PSHH-1210")
                # or by description keyword (e.g., "Gas Detector" matches description)
                if (
                    tag_upper.startswith(expected_upper)
                    or expected_upper in tag_upper
                    or expected_upper in desc_upper
                    or expected_upper.replace("_", " ") in desc_upper
                ):
                    relevant.append(safeguard)
                    break  # Avoid duplicates

        return relevant

    def _create_generic_deviation(self, equipment: Equipment, node: PIDNode) -> Deviation:
        """Create a minimal deviation for equipment types not in ontology."""
        return Deviation(
            deviation_id=str(uuid.uuid4()),
            node_id=node.node_id,
            equipment_tag=equipment.tag,
            guideword=Guideword.OTHER,
            parameter=DeviationParameter.PRESSURE,
            deviation=f"General Review Required - {equipment.equipment_type}",
            causes=["Equipment type not in standard ontology — manual review required"],
            consequences=["To be determined by SME"],
            safeguards=[],
            status=ReviewStatus.DRAFT,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            generated_by="ai_draft",
        )

    def get_deviation_summary(self, deviations: list[Deviation]) -> dict:
        """Generate a summary of all deviations for reporting."""
        equipment_tags = set()
        deviation_types = set()

        for d in deviations:
            equipment_tags.add(d.equipment_tag)
            deviation_types.add(d.deviation)

        return {
            "total_deviations": len(deviations),
            "equipment_count": len(equipment_tags),
            "equipment_tags": sorted(equipment_tags),
            "unique_deviation_types": sorted(deviation_types),
            "status_breakdown": {
                "draft": sum(1 for d in deviations if d.status == ReviewStatus.DRAFT),
                "approved": sum(1 for d in deviations if d.status == ReviewStatus.APPROVED),
                "rejected": sum(1 for d in deviations if d.status == ReviewStatus.REJECTED),
                "pending_review": sum(1 for d in deviations if d.status == ReviewStatus.PENDING_REVIEW),
            },
        }


# Module-level instance for convenience
deviation_generator = DeviationGeneratorService()
