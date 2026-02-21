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
    InstrumentContextItem, LLMContextItem, LLMContextSummary,
    GenerateConsequencesRequest, GenerateConsequencesResponse,
    DeviationConsequencesItem, OverpressureCalc,
    GenerateSafeguardsRequest, GenerateSafeguardsResponse, DeviationSafeguardsItem,
    SafeguardReviewItem,
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
    cause_included_tags: list[str] | None = None,
    cause_excluded_tags: list[str] | None = None,
) -> tuple[list[dict], list[LLMContextItem], list[LLMContextItem]]:
    """
    Split node instruments into three buckets for LLM cause generation:

      included_dicts   — raw dicts sent to the LLM (control valves / process devices)
      included_ctx     — LLMContextItem list for the transparency panel
      excluded_ctx     — LLMContextItem list for the transparency panel

    If cause_included_tags / cause_excluded_tags are provided (SME overrides),
    those explicit tag lists are used instead of prefix-based classification.

    Otherwise, the default prefix-based classification applies with BDV
    conditional logic: if any instrument has prefix "BDV" in the node,
    SDV/ESV/BDV/XV are moved from excluded to included.
    """
    included_dicts: list[dict] = []
    included_ctx: list[LLMContextItem] = []
    excluded_ctx: list[LLMContextItem] = []

    # --- SME override mode ---
    if cause_included_tags is not None or cause_excluded_tags is not None:
        include_set = set(cause_included_tags) if cause_included_tags else set()
        exclude_set = set(cause_excluded_tags) if cause_excluded_tags else set()

        for inst in instruments:
            inst_type = inst.instrument_type
            if inst.tag in include_set:
                included_dicts.append({
                    "tag": inst.tag,
                    "instrument_type": inst_type,
                    "setpoint": inst.setpoint,
                    "associated_equipment_tag": inst.associated_equipment_tag,
                })
                included_ctx.append(LLMContextItem(
                    tag=inst.tag,
                    instrument_type=inst_type,
                    reason="Included by SME override",
                ))
            elif inst.tag in exclude_set:
                excluded_ctx.append(LLMContextItem(
                    tag=inst.tag,
                    instrument_type=inst_type,
                    reason="Excluded by SME override",
                ))
            else:
                # Tags not in either list fall back to default classification
                prefix = _get_tag_prefix(inst.tag)
                if prefix in _LLM_EXCLUDED_PREFIXES:
                    excluded_ctx.append(LLMContextItem(
                        tag=inst.tag, instrument_type=inst_type,
                        reason=_excluded_reason_for_prefix(prefix),
                    ))
                else:
                    included_dicts.append({
                        "tag": inst.tag, "instrument_type": inst_type,
                        "setpoint": inst.setpoint,
                        "associated_equipment_tag": inst.associated_equipment_tag,
                    })
                    included_ctx.append(LLMContextItem(
                        tag=inst.tag, instrument_type=inst_type,
                        reason="Process instrument — included as potential cause context",
                    ))

        return included_dicts, included_ctx, excluded_ctx

    # --- Default classification with BDV conditional logic ---
    # Check if any instrument in the node has a BDV prefix
    bdv_in_node = any(
        _get_tag_prefix(inst.tag) == "BDV" for inst in instruments
    )
    # Prefixes to exempt from exclusion when BDV is detected
    _BDV_CONDITIONAL_PREFIXES = frozenset({"SDV", "ESV", "BDV", "XV"})

    for inst in instruments:
        prefix = _get_tag_prefix(inst.tag)
        inst_type = inst.instrument_type

        # BDV conditional: if BDV in node, SDV/ESV/BDV/XV are included for causes
        if bdv_in_node and prefix in _BDV_CONDITIONAL_PREFIXES:
            reason = "Shutdown/blowdown valve — included because BDV detected in node"
            included_dicts.append({
                "tag": inst.tag,
                "instrument_type": inst_type,
                "setpoint": inst.setpoint,
                "associated_equipment_tag": inst.associated_equipment_tag,
            })
            included_ctx.append(LLMContextItem(
                tag=inst.tag, instrument_type=inst_type, reason=reason,
            ))
        elif prefix in _LLM_EXCLUDED_PREFIXES:
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
            elif any(prefix.startswith(cv) for cv in ("LCV", "FCV", "TCV", "HCV")):
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
        node.instruments,
        cause_included_tags=request.cause_included_tags,
        cause_excluded_tags=request.cause_excluded_tags,
    )

    # Store instrument override config on node for audit trail
    if request.cause_included_tags is not None or request.cause_excluded_tags is not None:
        node_data["instrument_override_config"] = {
            "cause_included_tags": request.cause_included_tags,
            "cause_excluded_tags": request.cause_excluded_tags,
        }

    # Build equipment dicts once (always fully provided to LLM, with design pressure)
    equipment_data = [
        {
            "tag": eq.tag,
            "equipment_type": eq.equipment_type,
            "design_pressure": eq.design_pressure,
        }
        for eq in node.equipment
    ]

    # Build the LLM context summary for the frontend (node-level, kept for backward compat)
    llm_context = LLMContextSummary(
        included_equipment=equipment_data,
        included_instruments=included_ctx,
        excluded_instruments=excluded_ctx,
        upstream_pressure_psig=node.upstream_pressure_psig,
    )

    # Build a lookup for quick access to excluded tags at node level
    excluded_tags_set = {item.tag for item in excluded_ctx}

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

        # Per-deviation: instruments associated with this specific equipment
        equipment_instrument_dicts = [
            inst for inst in included_instrument_dicts
            if inst.get("associated_equipment_tag") == dev.equipment_tag
        ]
        included_tags_for_dev = {d["tag"] for d in equipment_instrument_dicts}

        # Build per-deviation InstrumentContextItem lists
        dev_included = [
            InstrumentContextItem(
                tag=d["tag"],
                instrument_type=d["instrument_type"],
                reason="Control valve — failure (open/closed) can directly cause a deviation",
                pid_reference=None,
            )
            for d in equipment_instrument_dicts
        ]
        dev_excluded = [
            InstrumentContextItem(
                tag=inst.tag,
                instrument_type=inst.instrument_type,
                reason=_excluded_reason_for_prefix(_get_tag_prefix(inst.tag)),
                pid_reference=inst.pid_reference,
            )
            for inst in node.instruments
            if inst.associated_equipment_tag == dev.equipment_tag
            and inst.tag in excluded_tags_set
            and inst.tag not in included_tags_for_dev
        ]

        # NOTE: No knowledge base context is used for cause generation.
        # Causes are grounded ONLY in P&ID information (equipment, instruments,
        # line connectivity, control loops, deviation locations).

        # Prepare P&ID structural data for this deviation's equipment
        node_line_connectivity = [
            lc.model_dump(mode="json") for lc in node.line_connectivity
        ] if node.line_connectivity else None
        node_control_loops = [
            cl.model_dump(mode="json") for cl in node.control_loops
        ] if node.control_loops else None
        node_deviation_locations = [
            dl.model_dump(mode="json") for dl in node.deviation_locations
        ] if node.deviation_locations else None

        # LLM: Enrich causes using filtered instruments + P&ID structural data
        try:
            safeguard_descriptions = [sg.description for sg in dev.safeguards]

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
                    included_instruments=dev_included,
                    excluded_instruments=dev_excluded,
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
                is_special_category=dev.requires_mandatory_sme_review,
                # Instruments scoped to this equipment; safeguards/transmitters excluded
                node_instruments=equipment_instrument_dicts,
                node_equipment=equipment_data,
                pid_summary=node.pid_summary,
                flow_description=node.flow_description,
                # P&ID structural data (line connectivity, control loops, deviation locations)
                line_connectivity=node_line_connectivity,
                control_loops=node_control_loops,
                deviation_locations=node_deviation_locations,
            )

            # Causes are already validated and flattened to plain strings
            # inside generate_deviation_content() — no further filtering needed.
            llm_causes = result.get("causes", [])

            # Use only LLM (P&ID-grounded) causes, no ontology merge
            merged_causes = llm_causes
        except Exception:
            # LLM failure: return empty list (no fallback to ontology)
            merged_causes = []

        deviation_causes_list.append(DeviationCausesItem(
            deviation_id=dev.deviation_id,
            equipment_tag=dev.equipment_tag,
            deviation=dev.deviation,
            guideword=dev.guideword.value,
            parameter=dev.parameter.value,
            causes=merged_causes,
            included_instruments=dev_included,
            excluded_instruments=dev_excluded,
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
    deviations = deviation_generator.generate_deviations_for_node(
        node, selected_deviation_types=request.selected_deviation_types
    )

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
            causes = []  # no fallback to ontology causes

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

        # RAG: retrieve PEC table (production-deck PAF consequence) for all deviations
        pec_table_context = ""
        try:
            pec_table_context = await knowledge_service.retrieve_pec_table_context()
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

        # Gather all P&ID instruments matched to this equipment for consequence context
        from app.services.safeguard_classifier import match_safeguards_to_equipment
        pid_instruments: list[dict] = []
        if equipment:
            pid_instruments = match_safeguards_to_equipment(
                equipment=equipment,
                instruments=node.instruments,
            )

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
                pec_table_context=pec_table_context or None,
                knowledge_context=knowledge_context if knowledge_context else None,
                is_special_category=dev.requires_mandatory_sme_review,
                pid_instruments=pid_instruments or None,
                pid_summary=node.pid_summary,
                flow_description=node.flow_description,
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
                pec=result.get("pec"),
                current_risk=result.get("current_risk"),
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


@router.post("/generate-safeguards", response_model=GenerateSafeguardsResponse)
async def generate_safeguards(request: GenerateSafeguardsRequest):
    """
    Generate safeguard (CME/KME) content for each deviation for SME review.

    This runs AFTER consequence approval (approve-consequences) and BEFORE full HAZOP generation.
    It uses:
      - SME-approved consequences (from node_data["approved_consequences"])
      - Safety instruments matched from P&ID (via safeguard_classifier.match_safeguards_to_equipment)
      - RAG: HSE Risk Assessment document for PR classification, CME Name, CME ID
      - LLM: enriches descriptions and adds mandatory safeguards (gas detection, deluge)

    PR classification is NOT hardcoded — it comes entirely from the HSE Risk Assessment doc via RAG.
    """
    from app.services.safeguard_classifier import match_safeguards_to_equipment

    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found.",
        )

    node = PIDNode(**node_data)
    approved_consequences = node_data.get("approved_consequences", {})
    deviation_safeguards_list: list[DeviationSafeguardsItem] = []

    # Rebuild deviations using the same selected types as the user chose
    deviations = deviation_generator.generate_deviations_for_node(
        node, selected_deviation_types=request.selected_deviation_types
    )

    # Retrieve CME/PR classification context from HSE Risk Assessment doc (once for all deviations)
    cme_knowledge_context = ""
    try:
        cme_knowledge_context = await knowledge_service.retrieve_cme_safeguard_context()
    except Exception:
        pass

    for dev in deviations:
        # Find equipment for this deviation
        equipment = None
        for eq in node.equipment:
            if eq.tag == dev.equipment_tag:
                equipment = eq
                break

        equipment_type = equipment.equipment_type if equipment else "Unknown"

        # Get approved causes and consequences for this deviation
        approved_causes: list[str] = []
        approved_cons: list[str] = []
        for _aid, adata in approved_consequences.items():
            if (adata.get("equipment_tag") == dev.equipment_tag
                    and adata.get("deviation") == dev.deviation):
                approved_causes = adata.get("causes", [])
                approved_cons = adata.get("consequences", [])
                break
        if not approved_causes:
            approved_causes = dev.causes

        # Match safety instruments from P&ID (returns raw dicts, no hardcoded PR classification)
        pid_instruments: list[dict] = []
        if equipment:
            pid_instruments = match_safeguards_to_equipment(
                equipment=equipment,
                instruments=node.instruments,
            )

        # Apply SME instrument overrides for safeguard generation if provided
        if request.safeguard_included_tags is not None or request.safeguard_excluded_tags is not None:
            sg_include_set = set(request.safeguard_included_tags or [])
            sg_exclude_set = set(request.safeguard_excluded_tags or [])
            if sg_include_set or sg_exclude_set:
                pid_instruments = [
                    inst for inst in pid_instruments
                    if inst.get("tag") not in sg_exclude_set
                ]
                # Add any instruments from include list that weren't matched by default
                matched_tags = {inst.get("tag") for inst in pid_instruments}
                for inst in node.instruments:
                    if inst.tag in sg_include_set and inst.tag not in matched_tags:
                        if not equipment or inst.associated_equipment_tag == equipment.tag:
                            pid_instruments.append({
                                "tag": inst.tag,
                                "instrument_type": inst.instrument_type,
                                "setpoint": inst.setpoint,
                                "associated_equipment_tag": inst.associated_equipment_tag,
                                "pid_reference": inst.pid_reference,
                            })

        # Drawing references from node
        drawing_refs = [node.drawing_number] if getattr(node, "drawing_number", None) else []

        # LLM: generate enriched safeguards with PR classification from HSE doc
        try:
            result = await openai_service.generate_safeguard_content(
                equipment_type=equipment_type,
                equipment_tag=dev.equipment_tag,
                deviation=dev.deviation,
                approved_causes=approved_causes,
                approved_consequences=approved_cons,
                pid_instruments=pid_instruments,
                cme_knowledge_context=cme_knowledge_context or None,
            )

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
                for sg in result.get("safeguards", [])
            ]
        except Exception:
            # Fallback: create minimal entries from P&ID instruments
            safeguard_items = [
                SafeguardReviewItem(
                    instrument_tag=inst["tag"],
                    description=(
                        f"{inst['instrument_type']} ({inst['tag']})"
                        if inst.get("instrument_type", "Other") != "Other"
                        else inst["tag"]
                    ),
                    pr_classification="Other",
                    mitigation_type=None,
                    pid_reference=inst.get("pid_reference"),
                    control_category=None,
                    cme_name=None,
                    cme_id=None,
                )
                for inst in pid_instruments
            ]

        # Get intermediate consequences and consequence fields from approved_consequences
        intermediate_cons: list[str] = []
        scenario_comments_val = None
        consequence_category_val = None
        pec_val = None
        current_risk_val = None
        for _aid, adata in approved_consequences.items():
            if (adata.get("equipment_tag") == dev.equipment_tag
                    and adata.get("deviation") == dev.deviation):
                intermediate_cons = adata.get("intermediate_consequences", [])
                scenario_comments_val = adata.get("scenario_comments")
                consequence_category_val = adata.get("consequence_category")
                pec_val = adata.get("pec")
                current_risk_val = adata.get("current_risk")
                break

        deviation_safeguards_list.append(DeviationSafeguardsItem(
            deviation_id=dev.deviation_id,
            equipment_tag=dev.equipment_tag,
            deviation=dev.deviation,
            guideword=dev.guideword.value,
            parameter=dev.parameter.value,
            causes=approved_causes,
            drawing_references=drawing_refs,
            intermediate_consequences=intermediate_cons,
            consequences=approved_cons,
            scenario_comments=scenario_comments_val,
            consequence_category=consequence_category_val,
            pec=pec_val,
            current_risk=current_risk_val,
            safeguards=safeguard_items,
        ))

    # Store pending safeguards on node for later retrieval
    node_data["pending_safeguards_review"] = [
        item.model_dump(mode="json") for item in deviation_safeguards_list
    ]
    await cosmos_client.save_node(node_data)

    return GenerateSafeguardsResponse(
        message=f"Generated safeguards for {len(deviation_safeguards_list)} deviations",
        node_id=request.node_id,
        deviation_safeguards=deviation_safeguards_list,
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

    # Load SME-approved causes, consequences and safeguards if available
    approved_causes = node_data.get("approved_causes")
    approved_consequences = node_data.get("approved_consequences")
    approved_safeguards = node_data.get("approved_safeguards")

    # Generate HAZOP
    report = await hazop_generator.generate_full_hazop(
        node=node,
        include_recommendations=request.include_recommendations,
        selected_deviation_types=request.selected_deviation_types,
        approved_causes=approved_causes,
        approved_consequences=approved_consequences,
        approved_safeguards=approved_safeguards,
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
