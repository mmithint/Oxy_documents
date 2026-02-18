"""
HAZOP Routes — Generation, Retrieval, and Risk Explanation

Endpoints:
  POST /api/hazop/generate              → Generate full HAZOP for a validated node
  POST /api/hazop/generate/quick        → Generate HAZOP without LLM (ontology only)
  GET  /api/hazop/report/{report_id}    → Get a HAZOP report by ID
  GET  /api/hazop/node/{node_id}        → Get latest HAZOP for a node
  GET  /api/hazop/report/{report_id}/summary  → Get report summary stats
  POST /api/hazop/explain-risk          → Get full risk calculation audit trail
  POST /api/hazop/regenerate-deviation  → Regenerate a single deviation
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.pid_models import PIDNode
from app.models.hazop_models import Safeguard
from app.models.api_models import (
    HAZOPGenerateRequest, HAZOPGenerateResponse,
    GenerateCausesRequest, GenerateCausesResponse, DeviationCausesItem,
)
from app.services.hazop_generator import hazop_generator, hazop_generator_offline
from app.services.deviation_generator import deviation_generator
from app.services.risk_engine import risk_engine, RISK_LEVEL_DEFINITIONS
from app.services.openai_service import openai_service
from app.services.knowledge_service import knowledge_service
from app.database.cosmos_client import cosmos_client

router = APIRouter()


# --- Request Models ---

class RiskExplainRequest(BaseModel):
    """Request body for risk calculation explanation."""
    paf_consequence: int | None = Field(None, ge=1, le=5)
    pd_lor_consequence: int | None = Field(None, ge=1, le=5)
    ecr_consequence: int | None = Field(None, ge=1, le=5)
    base_probability: int = Field(3, ge=1, le=5)
    safeguards: list[Safeguard] = Field(default_factory=list)


class RegenerateRequest(BaseModel):
    """Request to regenerate a single deviation."""
    report_id: str
    deviation_id: str
    node_id: str


# --- Endpoints ---

@router.post("/generate-causes", response_model=GenerateCausesResponse)
async def generate_causes(request: GenerateCausesRequest):
    """
    Generate causes (ontology + LLM) for each deviation, for SME review.

    This runs the deviation generator and LLM cause enrichment only,
    without generating the full HAZOP report. The SME reviews and edits
    causes before proceeding to full HAZOP generation.
    """
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found. Upload a P&ID first.",
        )

    node = PIDNode(**node_data)

    if not node.equipment:
        raise HTTPException(
            status_code=400,
            detail="Node has no equipment. SME must validate equipment list first.",
        )

    # Step 1: Generate deviations (rule-based ontology)
    deviations = deviation_generator.generate_deviations_for_node(
        node, selected_deviation_types=request.selected_deviation_types,
    )

    # Step 2: Enrich causes with LLM for each deviation
    deviation_causes_list: list[DeviationCausesItem] = []

    for dev in deviations:
        # Find the equipment for context
        equipment = None
        for eq in node.equipment:
            if eq.tag == dev.equipment_tag:
                equipment = eq
                break

        equipment_type = equipment.equipment_type if equipment else "Unknown"
        design_pressure = equipment.design_pressure if equipment else None
        design_temperature = equipment.design_temperature if equipment else None

        # RAG: Retrieve knowledge context
        knowledge_context = ""
        try:
            knowledge_context = await knowledge_service.retrieve_full_hazop_context(
                equipment_type=equipment_type,
                deviation=dev.deviation,
                limit=5,
            )
        except Exception:
            pass

        # LLM: Enrich causes only
        try:
            safeguard_descriptions = [sg.description for sg in dev.safeguards]

            # Build instrument and equipment dicts for LLM context
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
                {
                    "tag": eq.tag,
                    "equipment_type": eq.equipment_type,
                }
                for eq in node.equipment
            ]

            result = await openai_service.generate_deviation_content(
                equipment_type=equipment_type,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                design_pressure=design_pressure,
                design_temperature=design_temperature,
                existing_safeguards=safeguard_descriptions,
                knowledge_context=knowledge_context if knowledge_context else None,
                is_special_category=dev.requires_mandatory_sme_review,
                node_instruments=instruments_data,
                node_equipment=equipment_data,
            )

            # Merge LLM causes with ontology causes
            llm_causes = result.get("causes", [])
            merged_causes = hazop_generator._merge_lists(dev.causes, llm_causes)
        except Exception:
            # LLM failure: keep ontology causes as-is
            merged_causes = dev.causes

        deviation_causes_list.append(DeviationCausesItem(
            deviation_id=dev.deviation_id,
            equipment_tag=dev.equipment_tag,
            deviation=dev.deviation,
            guideword=dev.guideword.value,
            parameter=dev.parameter.value,
            causes=merged_causes,
        ))

    # Store pending causes on node for later retrieval
    node_data["pending_causes_review"] = [
        item.model_dump(mode="json") for item in deviation_causes_list
    ]
    node_data["selected_deviation_types"] = request.selected_deviation_types
    await cosmos_client.save_node(node_data)

    return GenerateCausesResponse(
        message=f"Generated causes for {len(deviation_causes_list)} deviations across {len(node.equipment)} equipment",
        node_id=request.node_id,
        deviation_causes=deviation_causes_list,
    )


@router.post("/generate", response_model=HAZOPGenerateResponse)
async def generate_hazop(request: HAZOPGenerateRequest):
    """
    Generate a full HAZOP report for a validated node.

    This triggers the complete pipeline:
      Deviations (rule-based) → RAG context → LLM enrichment →
      Risk scoring (deterministic) → Recommendations → Report

    The node must already exist in the database (uploaded and validated by SME).
    """
    # Fetch the validated node
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found. Upload a P&ID first.",
        )

    node = PIDNode(**node_data)

    if not node.equipment:
        raise HTTPException(
            status_code=400,
            detail="Node has no equipment. SME must validate equipment list first.",
        )

    # Load SME-approved causes if available
    approved_causes = node_data.get("approved_causes")

    # Generate HAZOP
    report = await hazop_generator.generate_full_hazop(
        node=node,
        include_recommendations=request.include_recommendations,
        selected_deviation_types=request.selected_deviation_types,
        approved_causes=approved_causes,
    )

    return HAZOPGenerateResponse(
        message=f"HAZOP generated: {report.total_deviations} deviations across {len(node.equipment)} equipment",
        report=report,
    )


@router.post("/generate/quick", response_model=HAZOPGenerateResponse)
async def generate_hazop_quick(request: HAZOPGenerateRequest):
    """
    Generate HAZOP using ontology only — no LLM calls.

    Useful for:
      - Quick draft when Azure OpenAI is unavailable
      - Testing the rule-based pipeline
      - Generating a skeleton for SME manual completion
    """
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(status_code=404, detail=f"Node {request.node_id} not found")

    node = PIDNode(**node_data)

    # Load SME-approved causes if available
    approved_causes = node_data.get("approved_causes")

    report = await hazop_generator_offline.generate_hazop_ontology_only(
        node=node,
        selected_deviation_types=request.selected_deviation_types,
        approved_causes=approved_causes,
    )

    return HAZOPGenerateResponse(
        message=f"Quick HAZOP generated (ontology only): {report.total_deviations} deviations",
        report=report,
    )


@router.get("/deviation-types")
async def get_deviation_types():
    """Return the list of 14 standard HAZOP deviation types."""
    from app.services.ontology_engine import get_standard_deviation_types
    return {"deviation_types": get_standard_deviation_types()}


@router.get("/report/{report_id}")
async def get_hazop_report(report_id: str):
    """Retrieve a HAZOP report by its ID."""
    report = await cosmos_client.get_hazop_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


@router.get("/node/{node_id}")
async def get_hazop_by_node(node_id: str):
    """Retrieve the latest HAZOP report for a specific node."""
    report = await cosmos_client.get_hazop_by_node(node_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"No HAZOP report found for node {node_id}",
        )
    return report


@router.get("/report/{report_id}/summary")
async def get_report_summary(report_id: str):
    """Get summary statistics for a HAZOP report."""
    report = await cosmos_client.get_hazop_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    deviations = report.get("deviations", [])

    # Count by status
    status_counts = {}
    risk_level_counts = {}
    equipment_tags = set()

    for dev in deviations:
        # Status
        status = dev.get("status", "draft")
        status_counts[status] = status_counts.get(status, 0) + 1

        # Equipment
        equipment_tags.add(dev.get("equipment_tag", "unknown"))

        # Risk levels
        risk = dev.get("risk", {})
        for category in ["paf", "pd_lor", "ecr"]:
            cat_risk = risk.get(category)
            if cat_risk:
                level = cat_risk.get("risk_level", "Unknown")
                risk_level_counts[level] = risk_level_counts.get(level, 0) + 1

    return {
        "report_id": report_id,
        "node_id": report.get("node_id"),
        "node_name": report.get("node_name"),
        "total_deviations": len(deviations),
        "status_breakdown": status_counts,
        "equipment_count": len(equipment_tags),
        "equipment_tags": sorted(equipment_tags),
        "risk_level_distribution": risk_level_counts,
        "requires_action": sum(
            1 for dev in deviations
            if _deviation_requires_action(dev)
        ),
    }


@router.post("/explain-risk")
async def explain_risk(request: RiskExplainRequest):
    """
    Get a full explainable audit trail for a risk calculation.

    Input consequence and probability values, and receive:
      - Severity definitions
      - Safeguard credits breakdown
      - Probability adjustment
      - Matrix lookup result
      - Risk level with action required

    This endpoint supports regulatory audit requirements.
    """
    explanation = risk_engine.explain_risk_calculation(
        paf_consequence=request.paf_consequence,
        pd_lor_consequence=request.pd_lor_consequence,
        ecr_consequence=request.ecr_consequence,
        base_probability=request.base_probability,
        safeguards=request.safeguards,
    )
    return explanation


@router.post("/regenerate-deviation")
async def regenerate_deviation(request: RegenerateRequest):
    """
    Regenerate a single deviation after SME requests revision.

    Re-runs the enrichment pipeline (RAG + LLM + risk) for one deviation
    without regenerating the entire report.
    """
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(status_code=404, detail=f"Node {request.node_id} not found")

    node = PIDNode(**node_data)

    deviation = await hazop_generator.regenerate_deviation(
        report_id=request.report_id,
        deviation_id=request.deviation_id,
        node=node,
    )

    if not deviation:
        raise HTTPException(
            status_code=404,
            detail=f"Deviation {request.deviation_id} not found in report {request.report_id}",
        )

    return {
        "message": "Deviation regenerated successfully",
        "deviation": deviation.model_dump(mode="json"),
    }


@router.get("/risk-matrix")
async def get_risk_matrix():
    """
    Return the risk matrix definitions and lookup tables.
    Used by the frontend to display risk information.
    """
    from app.services.risk_engine import (
        PAF_RISK_MATRIX, PD_LOR_RISK_MATRIX, ECR_RISK_MATRIX,
        PAF_SEVERITY, PD_LOR_SEVERITY, ECR_SEVERITY,
        PROBABILITY_LEVELS,
    )

    return {
        "matrices": {
            "paf": PAF_RISK_MATRIX,
            "pd_lor": PD_LOR_RISK_MATRIX,
            "ecr": ECR_RISK_MATRIX,
        },
        "severity_definitions": {
            "paf": PAF_SEVERITY,
            "pd_lor": PD_LOR_SEVERITY,
            "ecr": ECR_SEVERITY,
        },
        "probability_levels": PROBABILITY_LEVELS,
        "risk_level_definitions": RISK_LEVEL_DEFINITIONS,
    }


# --- Helpers ---

def _deviation_requires_action(deviation_data: dict) -> bool:
    """Check if a deviation has risk level C or higher."""
    risk = deviation_data.get("risk", {})
    for category in ["paf", "pd_lor", "ecr"]:
        cat_risk = risk.get(category)
        if cat_risk:
            level = cat_risk.get("risk_level", "A")
            if level in {"C", "D", "E"}:
                return True
    return False
