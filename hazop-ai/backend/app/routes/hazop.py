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
import re

from app.models.api_models import (
    HAZOPGenerateRequest, HAZOPGenerateResponse,
    GenerateCausesRequest, GenerateCausesResponse, DeviationCausesItem,
    LLMContextItem, LLMContextSummary,
    GenerateConsequencesRequest, GenerateConsequencesResponse,
    DeviationConsequencesItem, OverpressureCalc,
)
from app.services.hazop_generator import hazop_generator, hazop_generator_offline
from app.services.deviation_generator import deviation_generator
from app.services.risk_engine import risk_engine, RISK_LEVEL_DEFINITIONS
from app.services.openai_service import openai_service
from app.services.knowledge_service import knowledge_service
from app.database.cosmos_client import cosmos_client

router = APIRouter()


# ---------------------------------------------------------------------------
# Instrument classification — what gets sent to the LLM vs what doesn't
# ---------------------------------------------------------------------------

# Tag prefixes whose failure CANNOT be a root cause of a deviation.
# These are safeguards, passive measurement devices, or output-only devices.
_LLM_EXCLUDED_PREFIXES: frozenset[str] = frozenset({
    # Safety switches (safeguards — they respond to deviations, not cause them)
    "PSH", "PSL", "PSHH", "PSLL",
    "LSH", "LSL", "LSHH", "LSLL",
    "TSH", "TSL", "TSHH", "TSLL",
    "FSH", "FSL", "FSHH", "FSLL",
    # Safety / relief valves (safeguards)
    "PSV", "PRV", "SV", "RV",
    # Gas and fire detectors (safeguards)
    "GD", "GDS", "FD", "GAS",
    # ESD / shutdown / blowdown valves (safeguards; their failure to close is rare
    # and covered by the safety analysis separately — not a deviation root cause)
    "SDV", "ESV", "BDV", "XV",
    # Transmitters — passive measurement, not a cause
    "PT", "LT", "FT", "TT", "DPT", "AT", "WT", "FDT", "PDT", "PDPT",
    # Indicators — display only
    "PI", "LI", "FI", "TI", "PDI", "DPI", "TDI",
    # Gauges — display only
    "LG", "PG", "FG", "TG",
    # Alarms — output devices, not causes
    "PA", "LA", "FA", "TA", "XA", "GA",
})

# Human-readable reason strings for the transparency panel
_EXCLUDED_REASON: dict[str, str] = {
    "switch": "Safety switch — safeguard device, not a root cause",
    "safety_valve": "Safety / relief valve — safeguard device, not a root cause",
    "detector": "Gas / fire detector — safeguard device, not a root cause",
    "esd_valve": "ESD / shutdown valve — safeguard device, not a root cause",
    "transmitter": "Transmitter — passive measurement, failure does not cause the deviation",
    "indicator": "Indicator / gauge — display-only device, not a root cause",
    "alarm": "Alarm — output device, not a root cause",
    "unknown": "Monitoring / safety device — excluded to keep LLM focus on process causes",
}


def _get_tag_prefix(tag: str) -> str:
    """Extract the letter prefix from a tag, e.g. 'PSHH-1210' → 'PSHH'."""
    upper = tag.upper()
    # Split on first non-letter character (dash, underscore, digit)
    m = re.match(r"^([A-Z]+)", upper)
    return m.group(1) if m else upper


def _excluded_reason_for_prefix(prefix: str) -> str:
    """Map a tag prefix to a human-readable exclusion reason."""
    if any(prefix.startswith(p) for p in ("PSH", "PSL", "LSH", "LSL", "TSH", "TSL", "FSH", "FSL")):
        return _EXCLUDED_REASON["switch"]
    if prefix in ("PSV", "PRV", "SV", "RV"):
        return _EXCLUDED_REASON["safety_valve"]
    if prefix in ("GD", "GDS", "FD", "GAS"):
        return _EXCLUDED_REASON["detector"]
    if prefix in ("SDV", "ESV", "BDV", "XV"):
        return _EXCLUDED_REASON["esd_valve"]
    if prefix in ("PT", "LT", "FT", "TT", "DPT", "AT", "WT", "FDT", "PDT", "PDPT"):
        return _EXCLUDED_REASON["transmitter"]
    if prefix in ("PI", "LI", "FI", "TI", "PDI", "DPI", "TDI", "LG", "PG", "FG", "TG"):
        return _EXCLUDED_REASON["indicator"]
    if prefix in ("PA", "LA", "FA", "TA", "XA", "GA"):
        return _EXCLUDED_REASON["alarm"]
    return _EXCLUDED_REASON["unknown"]


def _classify_instruments_for_llm(
    instruments: list,
) -> tuple[list[dict], list[LLMContextItem], list[LLMContextItem]]:
    """
    Split node instruments into three buckets for LLM cause generation:

      included_dicts   — raw dicts sent to the LLM (control valves / process devices)
      included_ctx     — LLMContextItem list for the transparency panel
      excluded_ctx     — LLMContextItem list for the transparency panel

    Only instruments whose failure can plausibly be a root cause (e.g. control
    valves failing open/closed) are sent to the LLM.  Safeguards, transmitters,
    indicators, gauges, and alarms are excluded — they do not cause deviations,
    they respond to or measure them.
    """
    included_dicts: list[dict] = []
    included_ctx: list[LLMContextItem] = []
    excluded_ctx: list[LLMContextItem] = []

    for inst in instruments:
        prefix = _get_tag_prefix(inst.tag)
        inst_type = inst.instrument_type

        if prefix in _LLM_EXCLUDED_PREFIXES:
            excluded_ctx.append(LLMContextItem(
                tag=inst.tag,
                instrument_type=inst_type,
                reason=_excluded_reason_for_prefix(prefix),
            ))
        else:
            # Include — determine a clear reason for the transparency panel
            type_lower = inst_type.lower()
            if "control valve" in type_lower:
                reason = "Control valve — failure (open/closed) can directly cause a deviation"
            elif any(prefix.startswith(cv) for cv in ("PCV", "LCV", "FCV", "TCV", "HCV")):
                reason = "Control valve — failure (open/closed) can directly cause a deviation"
            else:
                reason = "Process instrument — included as potential cause context"

            included_dicts.append({
                "tag": inst.tag,
                "instrument_type": inst_type,
                "setpoint": inst.setpoint,
                "associated_equipment_tag": inst.associated_equipment_tag,
            })
            included_ctx.append(LLMContextItem(
                tag=inst.tag,
                instrument_type=inst_type,
                reason=reason,
            ))

    return included_dicts, included_ctx, excluded_ctx


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

    # Step 2: Classify instruments once for the whole node
    #   - included_instrument_dicts: control valves sent to LLM (can be root causes)
    #   - included_ctx / excluded_ctx: for the frontend transparency panel
    included_instrument_dicts, included_ctx, excluded_ctx = _classify_instruments_for_llm(
        node.instruments
    )

    # Build equipment dicts once (always fully provided to LLM, with design pressure)
    equipment_data = [
        {
            "tag": eq.tag,
            "equipment_type": eq.equipment_type,
            "design_pressure": eq.design_pressure,
        }
        for eq in node.equipment
    ]

    # Build the LLM context summary for the frontend
    llm_context = LLMContextSummary(
        included_equipment=equipment_data,
        included_instruments=included_ctx,
        excluded_instruments=excluded_ctx,
        upstream_pressure_psig=node.upstream_pressure_psig,
    )

    # Step 3: Enrich causes with LLM for each deviation
    deviation_causes_list: list[DeviationCausesItem] = []

    for dev in deviations:
        # Find the equipment for this deviation
        equipment = None
        for eq in node.equipment:
            if eq.tag == dev.equipment_tag:
                equipment = eq
                break

        equipment_type = equipment.equipment_type if equipment else "Unknown"
        design_pressure = equipment.design_pressure if equipment else None
        operating_pressure = equipment.operating_pressure if equipment else None
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

        # LLM: Enrich causes using filtered instruments + pressure context
        try:
            safeguard_descriptions = [sg.description for sg in dev.safeguards]

            # Only send instruments associated with this specific equipment
            # (control valves only — safeguards/transmitters/gauges already excluded)
            equipment_instrument_dicts = [
                inst for inst in included_instrument_dicts
                if inst.get("associated_equipment_tag") == dev.equipment_tag
            ]

            # If no control valves exist for this equipment and it's not a special
            # category (Human Factors / Previous Incidents), the LLM has nothing
            # instrument-specific to work with. Skip the call to avoid generic output.
            if not equipment_instrument_dicts and not dev.requires_mandatory_sme_review:
                deviation_causes_list.append(DeviationCausesItem(
                    deviation_id=dev.deviation_id,
                    equipment_tag=dev.equipment_tag,
                    deviation=dev.deviation,
                    guideword=dev.guideword.value,
                    parameter=dev.parameter.value,
                    causes=dev.causes,  # ontology causes only
                ))
                continue

            result = await openai_service.generate_deviation_content(
                equipment_type=equipment_type,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                design_pressure=design_pressure,
                operating_pressure=operating_pressure,
                upstream_pressure_psig=node.upstream_pressure_psig,
                design_temperature=design_temperature,
                existing_safeguards=safeguard_descriptions,
                knowledge_context=knowledge_context if knowledge_context else None,
                is_special_category=dev.requires_mandatory_sme_review,
                # Instruments scoped to this equipment; safeguards/transmitters excluded
                node_instruments=equipment_instrument_dicts,
                node_equipment=equipment_data,
            )

            # Causes are already validated and flattened to plain strings
            # inside generate_deviation_content() — no further filtering needed.
            llm_causes = result.get("causes", [])

            # Merge LLM causes with ontology causes
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
        llm_context=llm_context,
    )


@router.post("/generate-consequences", response_model=GenerateConsequencesResponse)
async def generate_consequences(request: GenerateConsequencesRequest):
    """
    Generate consequence content for each deviation for SME review.

    This runs AFTER cause approval (approve-causes) and BEFORE full HAZOP generation.
    It uses:
      - SME-approved causes (from node_data["approved_causes"])
      - Equipment design pressure + upstream_pressure_psig for overpressure calculation
      - RAG knowledge context (consequence docs, HSE Risk Assessment, Production Deck table)
      - LLM to generate intermediate consequences, final consequences, scenario comments,
        consequence category, PEC, and triggered safeguards (LOC → gas detection,
        Jet Fire → deluge)

    The generated consequences are stored for SME review before HAZOP generation.
    """
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found.",
        )

    node = PIDNode(**node_data)
    approved_causes = node_data.get("approved_causes", {})
    deviation_consequences_list: list[DeviationConsequencesItem] = []

    # Rebuild the list of deviations from approved_causes (same key structure)
    from app.services.deviation_generator import deviation_generator
    deviations = deviation_generator.generate_deviations_for_node(node)

    for dev in deviations:
        # Find equipment for this deviation
        equipment = None
        for eq in node.equipment:
            if eq.tag == dev.equipment_tag:
                equipment = eq
                break

        equipment_type = equipment.equipment_type if equipment else "Unknown"
        design_pressure = equipment.design_pressure if equipment else None
        operating_pressure = equipment.operating_pressure if equipment else None
        design_temperature = equipment.design_temperature if equipment else None

        # Get approved causes for this deviation (match by equipment_tag + deviation)
        causes: list[str] = []
        for _aid, adata in approved_causes.items():
            if (adata.get("equipment_tag") == dev.equipment_tag
                    and adata.get("deviation") == dev.deviation):
                causes = adata.get("causes", [])
                break
        if not causes:
            causes = dev.causes  # fall back to ontology causes

        # Calculate pressure ratio (pure math) — thresholds and hole sizes come from RAG
        pressure_ratio: float | None = None
        overpressure_table_context = ""
        if (dev.guideword.value == "HIGH"
                and dev.parameter.value == "PRESSURE"
                and design_pressure
                and node.upstream_pressure_psig):
            pressure_ratio = round(node.upstream_pressure_psig / design_pressure, 2)
            # Retrieve the pressure significance / hole size table from knowledge documents
            try:
                overpressure_table_context = await knowledge_service.retrieve_overpressure_table_context()
            except Exception:
                pass

        # RAG: retrieve consequence knowledge context
        knowledge_context = ""
        try:
            knowledge_context = await knowledge_service.retrieve_full_hazop_context(
                equipment_type=equipment_type,
                deviation=dev.deviation,
                limit=5,
            )
        except Exception:
            pass

        # LLM: generate consequence content
        try:
            result = await openai_service.generate_consequence_content(
                equipment_type=equipment_type,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                design_pressure=design_pressure,
                operating_pressure=operating_pressure,
                upstream_pressure_psig=node.upstream_pressure_psig,
                design_temperature=design_temperature,
                approved_causes=causes,
                pressure_ratio=pressure_ratio,
                overpressure_table_context=overpressure_table_context or None,
                knowledge_context=knowledge_context if knowledge_context else None,
                is_special_category=dev.requires_mandatory_sme_review,
            )

            # Build OverpressureCalc from LLM's table lookup result
            overpressure_calc: OverpressureCalc | None = None
            if pressure_ratio is not None and design_pressure and node.upstream_pressure_psig:
                op = result.get("overpressure_result") or {}
                overpressure_calc = OverpressureCalc(
                    max_credible_pressure=node.upstream_pressure_psig,
                    design_pressure=design_pressure,
                    ratio=pressure_ratio,
                    exceeds_2x=bool(op.get("is_vessel_rupture", False)),
                    assumed_leak_size=op.get("hole_size"),
                    significance=op.get("significance"),
                    consequence_description=op.get("consequence_description"),
                    source=op.get("source"),
                )

            # Drawing references from node
            drawing_refs = ([node.drawing_number] if getattr(node, "drawing_number", None) else
                            result.get("drawing_references", []))

            deviation_consequences_list.append(DeviationConsequencesItem(
                deviation_id=dev.deviation_id,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                guideword=dev.guideword.value,
                parameter=dev.parameter.value,
                causes=causes,
                drawing_references=drawing_refs,
                intermediate_consequences=result.get("intermediate_consequences", []),
                consequences=result.get("consequences", []),
                scenario_comments=result.get("scenario_comments"),
                consequence_category=result.get("consequence_category"),
                pec=result.get("personnel_exposure"),
                overpressure_calc=overpressure_calc,
            ))
        except Exception:
            # LLM failure: return ratio-only overpressure calc for SME to review
            fallback_calc: OverpressureCalc | None = None
            if pressure_ratio is not None and design_pressure and node.upstream_pressure_psig:
                fallback_calc = OverpressureCalc(
                    max_credible_pressure=node.upstream_pressure_psig,
                    design_pressure=design_pressure,
                    ratio=pressure_ratio,
                    exceeds_2x=False,
                    assumed_leak_size=None,
                    significance=None,
                    consequence_description=None,
                    source=None,
                )
            deviation_consequences_list.append(DeviationConsequencesItem(
                deviation_id=dev.deviation_id,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                guideword=dev.guideword.value,
                parameter=dev.parameter.value,
                causes=causes,
                drawing_references=[node.drawing_number] if getattr(node, "drawing_number", None) else [],
                overpressure_calc=fallback_calc,
            ))

    # Store pending consequences on node for later retrieval
    node_data["pending_consequences_review"] = [
        item.model_dump(mode="json") for item in deviation_consequences_list
    ]
    await cosmos_client.save_node(node_data)

    return GenerateConsequencesResponse(
        message=f"Generated consequences for {len(deviation_consequences_list)} deviations",
        node_id=request.node_id,
        deviation_consequences=deviation_consequences_list,
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

    # Load SME-approved causes and consequences if available
    approved_causes = node_data.get("approved_causes")
    approved_consequences = node_data.get("approved_consequences")

    # Generate HAZOP
    report = await hazop_generator.generate_full_hazop(
        node=node,
        include_recommendations=request.include_recommendations,
        selected_deviation_types=request.selected_deviation_types,
        approved_causes=approved_causes,
        approved_consequences=approved_consequences,
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
