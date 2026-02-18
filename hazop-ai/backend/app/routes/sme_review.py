"""
SME Review Routes — Equipment Validation & Deviation Approval Workflow

This is the human-in-the-loop layer.
No HAZOP output is final without SME approval.

Endpoints:
  POST /api/review/validate-equipment    → SME confirms/edits detected equipment
  PUT  /api/review/approve/{dev_id}      → SME approves a deviation
  PUT  /api/review/reject/{dev_id}       → SME rejects a deviation
  PUT  /api/review/revision/{dev_id}     → SME requests revision of a deviation
  PUT  /api/review/edit-deviation        → SME edits causes/consequences/recommendations
  PUT  /api/review/bulk-approve          → SME approves multiple deviations at once
  GET  /api/review/pending/{report_id}   → Get all pending deviations for a report
  GET  /api/review/stats/{report_id}     → Get review progress stats
"""

from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.pid_models import PIDNode
from app.models.hazop_models import ReviewStatus
from app.models.api_models import (
    EquipmentValidationRequest, EquipmentValidationResponse,
    ReviewRequest, ReviewResponse,
    DeviationEditRequest, DeviationEditResponse,
    ApproveCausesRequest, ApproveCausesResponse,
)
from app.database.cosmos_client import cosmos_client

router = APIRouter()


# --- Additional Request Models ---

class BulkApproveRequest(BaseModel):
    """Approve multiple deviations at once."""
    report_id: str
    deviation_ids: list[str]
    reviewer_name: str
    comments: str | None = None


# --- Equipment Validation ---

@router.post("/validate-equipment", response_model=EquipmentValidationResponse)
async def validate_equipment(request: EquipmentValidationRequest):
    """
    SME confirms or edits the detected equipment and instruments.

    This is the critical VALIDATE step in the Detect → Validate → Generate flow.
    The node is updated with SME-confirmed equipment before HAZOP generation.
    """
    # Fetch existing node
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found",
        )

    # Update with SME-confirmed equipment and instruments
    node_data["equipment"] = [
        eq.model_dump(mode="json") for eq in request.confirmed_equipment
    ]
    node_data["instruments"] = [
        inst.model_dump(mode="json") for inst in request.confirmed_instruments
    ]
    node_data["validated_by"] = request.sme_name
    node_data["validated_at"] = datetime.utcnow().isoformat()
    node_data["validation_comments"] = request.comments
    if request.upstream_pressure_psig is not None:
        node_data["upstream_pressure_psig"] = request.upstream_pressure_psig

    # Save updated node
    await cosmos_client.save_node(node_data)

    return EquipmentValidationResponse(
        message=f"Equipment validated by {request.sme_name}",
        node_id=request.node_id,
        equipment_count=len(request.confirmed_equipment),
        instrument_count=len(request.confirmed_instruments),
        validated=True,
    )


# --- Causes Review (Pre-Generation Step) ---

@router.post("/approve-causes", response_model=ApproveCausesResponse)
async def approve_causes(request: ApproveCausesRequest):
    """
    SME approves/edits causes for all deviations before full HAZOP generation.

    The approved causes are stored on the node document and will be used
    as-is during HAZOP generation (LLM will not overwrite them).
    """
    node_data = await cosmos_client.get_node(request.node_id)
    if not node_data:
        raise HTTPException(
            status_code=404,
            detail=f"Node {request.node_id} not found",
        )

    # Build approved_causes dict keyed by deviation_id
    approved_causes = {}
    for item in request.deviation_causes:
        approved_causes[item.deviation_id] = {
            "equipment_tag": item.equipment_tag,
            "deviation": item.deviation,
            "guideword": item.guideword,
            "parameter": item.parameter,
            "causes": item.causes,
            "approved_by": request.sme_name,
            "approved_at": datetime.utcnow().isoformat(),
        }

    node_data["approved_causes"] = approved_causes
    node_data["causes_approved_by"] = request.sme_name
    node_data["causes_approved_at"] = datetime.utcnow().isoformat()
    node_data["causes_approval_comments"] = request.comments

    await cosmos_client.save_node(node_data)

    return ApproveCausesResponse(
        message=f"Causes approved by {request.sme_name} for {len(request.deviation_causes)} deviations",
        node_id=request.node_id,
        deviations_count=len(request.deviation_causes),
        approved=True,
    )


# --- Deviation Review ---

@router.put("/approve/{deviation_id}", response_model=ReviewResponse)
async def approve_deviation(deviation_id: str, request: ReviewRequest):
    """
    SME approves a HAZOP deviation row.
    This confirms the causes, consequences, safeguards, and risk are acceptable.
    """
    if request.action != ReviewStatus.APPROVED:
        request.action = ReviewStatus.APPROVED

    success = await _update_deviation_review(
        deviation_id=deviation_id,
        status=ReviewStatus.APPROVED,
        reviewer=request.reviewer_name,
        comments=request.comments,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Deviation {deviation_id} not found in any report",
        )

    return ReviewResponse(
        message=f"Deviation approved by {request.reviewer_name}",
        deviation_id=deviation_id,
        new_status=ReviewStatus.APPROVED,
    )


@router.put("/reject/{deviation_id}", response_model=ReviewResponse)
async def reject_deviation(deviation_id: str, request: ReviewRequest):
    """
    SME rejects a HAZOP deviation row.
    The deviation will not be included in the final report.
    """
    success = await _update_deviation_review(
        deviation_id=deviation_id,
        status=ReviewStatus.REJECTED,
        reviewer=request.reviewer_name,
        comments=request.comments,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Deviation {deviation_id} not found in any report",
        )

    return ReviewResponse(
        message=f"Deviation rejected by {request.reviewer_name}",
        deviation_id=deviation_id,
        new_status=ReviewStatus.REJECTED,
    )


@router.put("/revision/{deviation_id}", response_model=ReviewResponse)
async def request_revision(deviation_id: str, request: ReviewRequest):
    """
    SME requests revision of a deviation.
    The deviation can then be regenerated via /hazop/regenerate-deviation.
    """
    success = await _update_deviation_review(
        deviation_id=deviation_id,
        status=ReviewStatus.REVISION_REQUESTED,
        reviewer=request.reviewer_name,
        comments=request.comments,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Deviation {deviation_id} not found in any report",
        )

    return ReviewResponse(
        message=f"Revision requested by {request.reviewer_name}",
        deviation_id=deviation_id,
        new_status=ReviewStatus.REVISION_REQUESTED,
    )


@router.put("/edit-deviation", response_model=DeviationEditResponse)
async def edit_deviation(request: DeviationEditRequest):
    """
    SME directly edits a deviation's causes, consequences, or recommendations.

    This allows SMEs to:
      - Add/remove causes the AI missed
      - Correct consequences
      - Add recommendations
      - Override risk scores (with justification)
    """
    # Find the report containing this deviation
    report = await _find_report_for_deviation(request.deviation_id)
    if not report:
        raise HTTPException(
            status_code=404,
            detail=f"Deviation {request.deviation_id} not found",
        )

    # Update the deviation
    updated = False
    for dev in report.get("deviations", []):
        if dev.get("deviation_id") == request.deviation_id:
            if request.causes is not None:
                dev["causes"] = request.causes
            if request.consequences is not None:
                dev["consequences"] = request.consequences
            if request.recommendations is not None:
                dev["recommendations"] = request.recommendations
            if request.risk_override is not None:
                dev["risk"] = request.risk_override.model_dump(mode="json")

            dev["updated_at"] = datetime.utcnow().isoformat()
            dev["edited_by"] = request.editor_name
            dev["status"] = ReviewStatus.PENDING_REVIEW.value
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Deviation not found in report")

    # Save updated report
    report["updated_at"] = datetime.utcnow().isoformat()
    await cosmos_client.save_hazop_report(report)

    return DeviationEditResponse(
        message=f"Deviation edited by {request.editor_name}",
        deviation_id=request.deviation_id,
        updated=True,
    )


@router.put("/bulk-approve")
async def bulk_approve(request: BulkApproveRequest):
    """Approve multiple deviations at once."""
    report = await cosmos_client.get_hazop_report(request.report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {request.report_id} not found")

    approved_count = 0
    deviation_ids_set = set(request.deviation_ids)

    for dev in report.get("deviations", []):
        if dev.get("deviation_id") in deviation_ids_set:
            dev["status"] = ReviewStatus.APPROVED.value
            dev["reviewed_by"] = request.reviewer_name
            dev["review_comments"] = request.comments
            dev["reviewed_at"] = datetime.utcnow().isoformat()
            approved_count += 1

    report["updated_at"] = datetime.utcnow().isoformat()
    await cosmos_client.save_hazop_report(report)

    return {
        "message": f"{approved_count} deviations approved by {request.reviewer_name}",
        "approved_count": approved_count,
        "total_requested": len(request.deviation_ids),
    }


# --- Query Endpoints ---

@router.get("/pending/{report_id}")
async def get_pending_deviations(report_id: str):
    """Get all deviations that are not yet approved."""
    report = await cosmos_client.get_hazop_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    pending = [
        dev for dev in report.get("deviations", [])
        if dev.get("status") != ReviewStatus.APPROVED.value
    ]

    return {
        "report_id": report_id,
        "pending_count": len(pending),
        "deviations": pending,
    }


@router.get("/stats/{report_id}")
async def get_review_stats(report_id: str):
    """Get review progress statistics for a report."""
    report = await cosmos_client.get_hazop_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    deviations = report.get("deviations", [])
    total = len(deviations)

    if total == 0:
        return {
            "report_id": report_id,
            "total": 0,
            "progress_percent": 0,
            "breakdown": {},
        }

    breakdown = {}
    for dev in deviations:
        status = dev.get("status", "draft")
        breakdown[status] = breakdown.get(status, 0) + 1

    approved = breakdown.get(ReviewStatus.APPROVED.value, 0)
    progress = round((approved / total) * 100, 1)

    return {
        "report_id": report_id,
        "total": total,
        "approved": approved,
        "progress_percent": progress,
        "breakdown": breakdown,
        "is_complete": approved == total,
        "reviewers": list(set(
            dev.get("reviewed_by")
            for dev in deviations
            if dev.get("reviewed_by")
        )),
    }


# --- Helpers ---

async def _update_deviation_review(
    deviation_id: str,
    status: ReviewStatus,
    reviewer: str,
    comments: str | None,
) -> bool:
    """Find and update a deviation's review status across all reports."""
    report = await _find_report_for_deviation(deviation_id)
    if not report:
        return False

    for dev in report.get("deviations", []):
        if dev.get("deviation_id") == deviation_id:
            dev["status"] = status.value
            dev["reviewed_by"] = reviewer
            dev["review_comments"] = comments
            dev["reviewed_at"] = datetime.utcnow().isoformat()
            break

    report["updated_at"] = datetime.utcnow().isoformat()
    await cosmos_client.save_hazop_report(report)
    return True


async def _find_report_for_deviation(deviation_id: str) -> dict | None:
    """Search all reports to find which one contains a specific deviation."""
    # Search by deviation_id within reports
    report = cosmos_client.hazop_reports_collection.find_one(
        {"deviations.deviation_id": deviation_id},
        {"_id": 0},
    )
    return report
