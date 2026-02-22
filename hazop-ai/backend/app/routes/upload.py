"""
Upload Routes — P&ID and Knowledge Document Upload

Endpoints:
  POST /api/upload/pid              → Upload P&ID (PDF/image/DWG), parse, extract equipment & instruments
  POST /api/upload/knowledge        → Upload knowledge doc, chunk, embed, store for RAG
  GET  /api/upload/pid/list         → List all uploaded P&ID files
  GET  /api/upload/capabilities     → Server capability flags (DWG conversion available, etc.)
"""

import asyncio
import re
import time

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.services.blob_storage import blob_storage
from app.services.document_intelligence import doc_intelligence
from app.services.knowledge_service import knowledge_service
from app.services.dwg_converter import dwg_converter, DWGConversionError
from app.services.openai_service import openai_service
from app.services.claude_service import claude_service
from app.database.cosmos_client import cosmos_client
from app.models.api_models import UploadResponse, ExtractionCompareResponse

router = APIRouter()

# MIME types accepted for direct processing (no conversion needed)
_DIRECT_TYPES = {
    "application/pdf", "image/png", "image/jpeg",
    "image/tiff", "image/bmp",
}

# MIME types that indicate a DWG file (browsers report these inconsistently)
_DWG_TYPES = {
    "application/dwg", "application/acad", "application/x-acad",
    "image/vnd.dwg", "image/x-dwg", "application/vnd.dwg",
}


@router.post("/pid", response_model=UploadResponse)
async def upload_pid(
    file: UploadFile = File(..., description="P&ID file (PDF, image, or DWG)"),
    node_id: str = Form(default=None, description="Optional node ID to assign"),
):
    """
    Upload a P&ID file for processing.

    Accepted formats: PDF, PNG, JPEG, TIFF, BMP, DWG.

    DWG files are automatically converted to PDF via ODA File Converter before
    being passed through the Document Intelligence + GPT-4 Vision pipeline.

    Flow:
      1. (DWG only) Convert DWG → PDF using ODA File Converter
      2. Upload original file to Azure Blob Storage
      3. Parse with Azure Document Intelligence
      4. Extract equipment and instrument tags
      5. Store extracted node in Cosmos DB
      6. Return structured extraction result for SME review
    """
    original_filename = file.filename or "unknown.pdf"
    content_type = file.content_type or ""

    # Determine whether this is a DWG file
    is_dwg = dwg_converter.is_dwg_file(original_filename, content_type)

    # Validate file type for non-DWG uploads
    if not is_dwg and content_type and content_type not in _DIRECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type: {content_type}. "
                "Allowed: PDF, PNG, JPEG, TIFF, BMP, DWG"
            ),
        )

    # Read file content
    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Step 1: Upload original file to Blob Storage (archival — always the original)
    blob_result = await blob_storage.upload_pid_file(
        file_content=file_content,
        original_filename=original_filename,
        content_type=content_type or "application/octet-stream",
    )

    if is_dwg:
        # ---- DWG Path: DWG → DXF → direct entity extraction (no OCR, no Vision) ----
        # Converting to DXF gives exact CAD text strings — no OCR misreads.
        # Spatial coordinates and layer names help the LLM associate instruments
        # with their equipment.
        if not dwg_converter.is_available():
            raise HTTPException(
                status_code=422,
                detail=(
                    "DWG file detected but ODA File Converter is not installed on this server. "
                    "Please install ODA File Converter and set ODA_CONVERTER_PATH in the server "
                    ".env file, or convert the DWG to PDF manually before uploading. "
                    "Download: https://www.opendesign.com/guestfiles/oda_file_converter"
                ),
            )

        try:
            dxf_content, dxf_filename = await dwg_converter.convert_dwg_to_dxf(
                dwg_content=file_content,
                filename=original_filename,
            )
        except DWGConversionError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"DWG → DXF conversion failed: {exc}",
            )

        # Step 2a: Extract directly from DXF entities
        extraction = await doc_intelligence.parse_pid_from_dxf(
            dxf_content=dxf_content,
            source_filename=dxf_filename,
            node_id=node_id,
        )
    else:
        # ---- PDF / image path: Azure Document Intelligence OCR + GPT-4 Vision ----
        extraction = await doc_intelligence.parse_pid(
            file_content=file_content,
            source_filename=original_filename,
            node_id=node_id,
        )

    # Step 3: Update blob URL + normalize drawing number in extraction
    for node in extraction.nodes:
        node.pid_drawings.append(blob_result["blob_url"])
        node.drawing_number = _normalize_drawing_number(node.drawing_number)

    # Step 4: Store extracted nodes in Cosmos DB (include LLM data)
    for node in extraction.nodes:
        node_doc = node.model_dump(mode="json")
        node_doc["ocr_chunks"] = extraction.ocr_chunks
        node_doc["llm_raw_output"] = extraction.llm_raw_output
        await cosmos_client.save_node(node_doc)

    # Step 5: Return result
    dwg_note = " (DWG → DXF entity extraction, no OCR)" if is_dwg else ""
    return UploadResponse(
        message=(
            f"P&ID parsed successfully{dwg_note}. "
            f"Found {sum(len(n.equipment) for n in extraction.nodes)} equipment "
            f"and {sum(len(n.instruments) for n in extraction.nodes)} instruments."
        ),
        file_name=original_filename,
        blob_url=blob_result["blob_url"],
        nodes=extraction.nodes,
        confidence_score=extraction.confidence_score,
        ocr_chunks=extraction.ocr_chunks,
        llm_raw_output=extraction.llm_raw_output,
        vision_raw_output=extraction.vision_raw_output,
        merge_summary=extraction.merge_summary,
    )


@router.post("/knowledge")
async def upload_knowledge_document(
    file: UploadFile = File(..., description="Knowledge document (PDF, DOCX, or XLSX)"),
    document_type: str = Form(
        ...,
        description="Document type: consequence_guidance, risk_matrix, barrier_philosophy, sop, hazop_reference",
    ),
):
    """
    Upload a knowledge document for RAG retrieval.

    Flow:
      1. Upload to Blob Storage
      2. Extract text with Document Intelligence
      3. Chunk text into manageable pieces
      4. Generate embeddings with Azure OpenAI
      5. Store chunks + embeddings in Cosmos DB (vector search enabled)
    """
    valid_types = {
        "consequence_guidance", "risk_matrix", "barrier_philosophy",
        "sop", "hazop_reference",
    }
    if document_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid document_type. Allowed: {', '.join(valid_types)}",
        )

    allowed_knowledge_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if file.content_type and file.content_type not in allowed_knowledge_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: PDF, DOCX, XLSX",
        )

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Upload to blob
    blob_result = await blob_storage.upload_knowledge_document(
        file_content=file_content,
        original_filename=file.filename or "unknown.pdf",
        content_type=file.content_type or "application/pdf",
    )

    # Ingest: parse → chunk → embed → store
    ingestion_result = await knowledge_service.ingest_document(
        file_content=file_content,
        filename=file.filename or "unknown.pdf",
        document_type=document_type,
    )

    return {
        "message": "Knowledge document ingested successfully",
        "blob_url": blob_result["blob_url"],
        **ingestion_result,
    }


@router.get("/pid/list")
async def list_pid_files():
    """List all uploaded P&ID files."""
    files = await blob_storage.list_pid_files()
    return {"files": files, "count": len(files)}


@router.get("/capabilities")
async def get_upload_capabilities():
    """
    Return server-side upload capabilities.

    Includes whether ODA File Converter is installed (needed for DWG support).
    """
    oda_available = dwg_converter.is_available()
    return {
        "accepted_formats": ["PDF", "PNG", "JPEG", "TIFF", "BMP", "DWG"],
        "dwg_conversion": {
            "available": oda_available,
            "note": (
                "ODA File Converter is installed — DWG files will be automatically converted to PDF."
                if oda_available
                else (
                    "ODA File Converter is NOT installed. DWG files cannot be processed. "
                    "Install from https://www.opendesign.com/guestfiles/oda_file_converter "
                    "and set ODA_CONVERTER_PATH in the server .env file."
                )
            ),
        },
    }


@router.get("/nodes")
async def list_nodes():
    """List all extracted nodes."""
    nodes = await cosmos_client.get_all_nodes()
    return {"nodes": nodes, "count": len(nodes)}


@router.post("/pid/compare", response_model=ExtractionCompareResponse)
async def compare_pid_extraction(file: UploadFile = File(...)):
    """
    Upload a P&ID PDF and compare GPT-4 vs Claude extraction side by side.

    OCR runs once; both models receive identical inputs in parallel.
    Response includes per-model equipment/instrument lists plus a tag diff summary.
    """
    if file.content_type and file.content_type not in _DIRECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                "The compare endpoint accepts PDF and image files only (not DWG)."
            ),
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Step 1: OCR + image conversion — run once, share between both models
    pid_inputs = await doc_intelligence.prepare_pid_inputs(file_bytes, file.filename or "unknown.pdf")
    chunks: list[str] = pid_inputs["chunks"]
    images: list[dict] = pid_inputs["images_base64"]
    ocr_hint: str | None = pid_inputs["raw_text"][:3000] if pid_inputs.get("raw_text") else None

    # Step 2: Run GPT-4 and Claude in parallel
    async def run_gpt():
        t0 = time.monotonic()
        try:
            text_res = await openai_service.extract_pid_data(chunks, file.filename or "unknown.pdf")
            vision_res = await openai_service.extract_pid_data_with_vision(
                images, file.filename or "unknown.pdf", ocr_hint
            )
            merged = _simple_merge(text_res, vision_res)
            return merged, int((time.monotonic() - t0) * 1000), None
        except Exception as e:
            return {}, int((time.monotonic() - t0) * 1000), str(e)

    async def run_claude():
        t0 = time.monotonic()
        try:
            text_res = await claude_service.extract_pid_data(chunks, file.filename or "unknown.pdf")
            vision_res = await claude_service.extract_pid_data_with_vision(
                images, file.filename or "unknown.pdf", ocr_hint
            )
            merged = _simple_merge(text_res, vision_res)
            return merged, int((time.monotonic() - t0) * 1000), None
        except Exception as e:
            return {}, int((time.monotonic() - t0) * 1000), str(e)

    (gpt_data, gpt_ms, gpt_err), (claude_data, claude_ms, claude_err) = await asyncio.gather(
        run_gpt(), run_claude()
    )

    # Step 3: Build tag diff summary
    gpt_eq_tags = {e.get("tag", "").upper() for e in gpt_data.get("equipment", []) if e.get("tag")}
    gpt_inst_tags = {i.get("tag", "").upper() for i in gpt_data.get("instruments", []) if i.get("tag")}
    gpt_tags = gpt_eq_tags | gpt_inst_tags

    claude_eq_tags = {e.get("tag", "").upper() for e in claude_data.get("equipment", []) if e.get("tag")}
    claude_inst_tags = {i.get("tag", "").upper() for i in claude_data.get("instruments", []) if i.get("tag")}
    claude_tags = claude_eq_tags | claude_inst_tags

    return ExtractionCompareResponse(
        file_name=file.filename or "unknown.pdf",
        ocr_chunks_count=len(chunks),
        pages_count=len(images),
        gpt_equipment=gpt_data.get("equipment", []),
        gpt_instruments=gpt_data.get("instruments", []),
        gpt_duration_ms=gpt_ms,
        gpt_error=gpt_err,
        claude_equipment=claude_data.get("equipment", []),
        claude_instruments=claude_data.get("instruments", []),
        claude_duration_ms=claude_ms,
        claude_error=claude_err,
        tags_in_both=sorted(gpt_tags & claude_tags),
        tags_only_in_gpt=sorted(gpt_tags - claude_tags),
        tags_only_in_claude=sorted(claude_tags - gpt_tags),
    )


def _simple_merge(text_res: dict, vision_res: dict) -> dict:
    """
    Merge text and vision extraction results by deduplicating on tag.
    Text result is primary; vision adds tags not already found.
    """
    eq_seen: set[str] = set()
    equipment: list[dict] = []
    for item in text_res.get("equipment", []):
        tag = (item.get("tag") or "").strip().upper()
        if tag and tag not in eq_seen:
            eq_seen.add(tag)
            equipment.append(item)
    for item in vision_res.get("equipment", []):
        tag = (item.get("tag") or "").strip().upper()
        if tag and tag not in eq_seen:
            eq_seen.add(tag)
            equipment.append(item)

    inst_seen: set[str] = set()
    instruments: list[dict] = []
    for item in text_res.get("instruments", []):
        tag = (item.get("tag") or "").strip().upper()
        if tag and tag not in inst_seen:
            inst_seen.add(tag)
            instruments.append(item)
    for item in vision_res.get("instruments", []):
        tag = (item.get("tag") or "").strip().upper()
        if tag and tag not in inst_seen:
            inst_seen.add(tag)
            instruments.append(item)

    return {
        **text_res,
        "equipment": equipment,
        "instruments": instruments,
    }


def _normalize_drawing_number(raw: str | None) -> str | None:
    """
    Strip trailing revision suffix from an APC / drawing number.
    e.g. "APC No. 4020(c)" → "APC No. 4020"
         "DWG-4020-A"      → "DWG-4020-A"  (no trailing parens, unchanged)
    """
    if not raw:
        return None
    cleaned = re.sub(r'\s*\([^)]+\)\s*$', '', raw.strip())
    return cleaned or None


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    """Get a specific node by ID."""
    node = await cosmos_client.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return node
